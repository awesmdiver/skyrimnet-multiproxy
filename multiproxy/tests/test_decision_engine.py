"""The local decision model's engine manager (decision_engine.py), against a fake llama-server that speaks llama.cpp's own `/v1/systemone`: install, where it runs
(never crowding the card), a decision passed straight through, several requests at once, the waiting limit, the questions the engine can't take (turned into
"pass it on"), the earlier model's leftovers, stopping, uninstalling."""
import asyncio
import json
import os
import time
from pathlib import Path

import pytest

import decision_engine as de
from tests.decision_helpers import hardware, installed_engine, make_engine

STATE = "Lydia drew her sword and fought two wolves outside Whiterun. The last wolf has just died and the road is quiet."
NOUL = {"type": "noul", "instructions": "Is combat still going on?"}
CHOICE = {"type": "choice", "instructions": "What is Lydia's mood?", "criteria": {"calm": "relaxed", "tense": "on edge", "angry": "furious"}}
SCORE = {"type": "score", "instructions": "How urgent?", "criteria": ["none", "low", "high"]}


@pytest.fixture
def knobs(monkeypatch):
    def set_(**env):
        for k, v in env.items():
            monkeypatch.setenv(k, str(v))
    return set_


def count_launches(engine):
    seen = []
    inner = engine._launcher

    def wrapped(exe, model, port, mode, slots, context):
        seen.append({"mode": mode, "port": port, "slots": slots, "context": context, "model": model.name})
        return inner(exe, model, port, mode, slots, context)
    engine._launcher = wrapped
    return seen


# ---- where it runs ------------------------------------------------------------------------------------------------------------------------------
class TestChooseRunOn:
    NEED = int(3.1 * de.GIB)

    def test_automatic_uses_the_card_only_with_room_to_spare(self):
        mode, why = de.choose_run_on("automatic", hardware(free_vram_gb=10), self.NEED)
        assert mode == "gpu" and "room to spare" in why
        mode, why = de.choose_run_on("automatic", hardware(free_vram_gb=4.0), self.NEED)           # 3.1 GB needed + 1.5 GB margin = 4.6 GB
        assert mode == "cpu" and "system memory" in why and why == "Running on system memory (the graphics card is too full)."
        assert de.choose_run_on("automatic", hardware(free_vram_gb=4.7), self.NEED)[0] == "gpu"

    def test_the_margin_is_a_gigabyte_and_a_half(self):
        assert de.AUTO_GPU_MARGIN_BYTES == int(1.5 * de.GIB)

    def test_automatic_without_a_card_or_a_free_memory_reading_stays_off_the_card(self):
        assert de.choose_run_on("automatic", hardware(gpu=False, free_vram_gb=None), self.NEED)[0] == "cpu"
        mode, why = de.choose_run_on("automatic", hardware(free_vram_gb=None, vendor="amd"), self.NEED)
        assert mode == "cpu" and "graphics card free for the game" in why

    def test_the_players_choice_is_followed(self):
        assert de.choose_run_on("gpu", hardware(free_vram_gb=0.5), self.NEED) == ("gpu", "You chose the graphics card.")
        assert de.choose_run_on("cpu", hardware(free_vram_gb=30), self.NEED) == ("cpu", "You chose system memory.")

    def test_graphics_card_without_a_card_or_with_the_no_card_engine_is_system_memory(self):
        assert de.choose_run_on("gpu", hardware(gpu=False), self.NEED)[0] == "cpu"
        assert de.choose_run_on("gpu", hardware(), self.NEED, engine_kind="cpu")[0] == "cpu"

    def test_the_waiting_limit_defaults_by_place_and_can_be_set(self):
        assert de.limit_for("gpu") == de.GPU_LIMIT_SECONDS == 0.8 and de.limit_for("cpu") == de.CPU_LIMIT_SECONDS == 6.0
        assert de.limit_for("gpu", 2.5) == 2.5 and de.limit_for("cpu", 0) == 6.0
        assert de.limit_for("gpu", 0.01) == de.GPU_LIMIT_SECONDS, "a setting too small to mean anything is ignored"


# ---- install ------------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestInstall:
    async def test_install_puts_the_model_in_place_keeps_the_download_and_ends_ready(self, tmp_path):
        engine, _, _ = make_engine(tmp_path)
        assert engine.status()["state"] == "not_installed"
        job = engine.start_install()
        assert engine.status()["state"] == "installing" and job.total == engine.spec().total_bytes()
        await job.task
        assert job.state == "done" and job.done == job.total
        s = engine.status()
        assert s["state"] == "ready" and s["ready"] and s["installed"] and s["model"] == "kev-4b"
        d = tmp_path / "decisions" / "kev-4b"
        assert sorted(p.name for p in d.iterdir()) == ["model-kev-4b.gguf"]
        assert sorted(f["name"] for f in s["downloads"]["files"]) == ["model-kev-4b.gguf"]
        assert engine.state_file.exists() and engine._read_state()["model"] == "kev-4b"
        await engine.close()

    async def test_the_steps_are_engine_model_warmup_in_that_order(self, tmp_path):
        engine, _, _ = make_engine(tmp_path)
        steps = []
        job = engine.start_install()
        while job.state == "running":
            if not steps or steps[-1] != job.step:
                steps.append(job.step)
            await asyncio.sleep(0.005)
        assert steps[0] == "engine" and [s for s in steps if s in ("model", "warmup")] == [s for s in ("model", "warmup") if s in steps]
        assert steps[-1] in ("model", "warmup") and "addon" not in steps
        await engine.close()

    async def test_a_second_install_takes_what_is_kept_and_a_clear_of_the_kept_files_breaks_nothing(self, tmp_path):
        engine, _, src = await installed_engine(tmp_path)
        await engine.uninstall()
        for f in src.iterdir():                                     # nothing to download from any more
            f.unlink()
        job = engine.start_install()
        await job.task
        assert job.state == "done"
        engine.store.clear()
        assert engine.installed() and engine.status()["ready"], "installed files are links or copies, never pointers into the kept folder"
        await engine.close()

    async def test_a_missing_file_ends_in_a_plain_error_and_nothing_is_left_half_installed(self, tmp_path):
        engine, _, src = make_engine(tmp_path)
        (src / "model-kev-4b.gguf").unlink()
        job = engine.start_install()
        await job.task
        assert job.state == "error" and job.error == de.DOWNLOAD_FAILED        # plain words, no file name or folder in them
        assert engine.status()["state"] == "not_installed"
        assert engine.status()["downloads"]["files"] == [], "nothing arrived, so nothing is kept"
        await engine.close()

    async def test_the_llama_engine_is_fetched_first_when_it_is_not_there_and_a_failure_says_so(self, tmp_path):
        engine, _, _ = make_engine(tmp_path)
        calls = []

        async def failing(progress):
            calls.append(1)
            raise de.DecisionError(502, "The llama.cpp engine didn't download.")
        engine._ensure_engine = failing
        job = engine.start_install()
        await job.task
        assert calls and job.state == "error" and job.error == "The llama.cpp engine didn't download."
        assert engine.status()["state"] == "not_installed"

    async def test_a_cancelled_install_says_so_and_carries_on_later(self, tmp_path):
        engine, _, _ = make_engine(tmp_path)
        gate = asyncio.Event()

        async def slow(progress):
            await gate.wait()
            return tmp_path / "llama-server.exe"
        engine._ensure_engine = slow
        job = engine.start_install()
        await asyncio.sleep(0.05)
        assert engine.cancel_install() is True
        await asyncio.gather(job.task, return_exceptions=True)
        assert job.state == "cancelled" and engine.status()["state"] == "not_installed"
        assert engine.cancel_install() is False
        with pytest.raises(de.DecisionError) as e:
            engine.start_install(); engine.start_install()
        assert e.value.status == 409
        engine.cancel_install()

    async def test_no_room_on_the_drive_refuses_but_little_memory_only_warns(self, tmp_path):
        engine, _, _ = make_engine(tmp_path, hw=hardware(free_disk_gb=0.1))
        fit = engine.fit_check()
        assert fit["verdict"] == "refuse" and "isn't enough room" in fit["reasons"][0]
        with pytest.raises(de.DecisionError):
            engine.start_install()
        real = de.DecisionEngine(lambda: tmp_path / "real", engine.store, lambda: {"model": "kev-4b"}, lambda: tmp_path, None, hardware_fn=lambda f: hardware(free_disk_gb=1.0))
        fit = real.fit_check()
        assert fit["verdict"] == "refuse" and 3.0 * de.GIB < fit["needDiskBytes"] < 3.6 * de.GIB, "the real Kev-4B needs about 3.3 GB of room"
        engine2, _, _ = make_engine(tmp_path / "b", hw=hardware(free_vram_gb=0.5, free_ram_gb=1.0))
        fit = engine2.fit_check()
        assert fit["verdict"] == "warn" and fit["runOn"] == "cpu" and any("system memory" in w for w in fit["warnings"])
        engine3, _, _ = make_engine(tmp_path / "c", hw=hardware(gpu=False, free_vram_gb=None))
        assert engine3.fit_check()["verdict"] == "warn" and "No graphics card" in engine3.fit_check()["warnings"][0]


# ---- starting: where, and what the status says --------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestStart:
    async def test_automatic_puts_it_on_the_card_when_there_is_room_and_says_why(self, tmp_path):
        engine, _, _ = make_engine(tmp_path, hw=hardware(free_vram_gb=20))
        seen = count_launches(engine)
        await (engine.start_install()).task
        s = engine.status()
        assert seen[0]["mode"] == "gpu" and s["runOn"] == "gpu" and s["runOnLabel"] == "Graphics card" and "room to spare" in s["runOnReason"]
        assert seen[0]["slots"] == 4 and seen[0]["context"] == de.CONTEXT_PER_SLOT * 4 and seen[0]["model"] == "model-kev-4b.gguf"
        await engine.close()

    async def test_automatic_starts_on_system_memory_when_the_card_is_nearly_full_and_never_fails_for_it(self, tmp_path):
        engine, _, _ = make_engine(tmp_path, hw=hardware(free_vram_gb=0.4))
        seen = count_launches(engine)
        job = engine.start_install()
        await job.task
        s = engine.status()
        assert job.state == "done" and s["state"] == "ready" and seen[0]["mode"] == "cpu" and s["runOn"] == "cpu" and s["runOnLabel"] == "System memory"
        assert s["runOnReason"] == "Running on system memory (the graphics card is too full)." and s["limitSeconds"] == de.CPU_LIMIT_SECONDS
        await engine.close()

    async def test_the_players_setting_decides_at_the_next_start(self, tmp_path):
        engine, settings, _ = await installed_engine(tmp_path, hw=hardware(free_vram_gb=20))
        seen = count_launches(engine)
        settings["run_on"] = "cpu"
        await engine.restart()
        assert seen[-1]["mode"] == "cpu" and engine.status()["runOn"] == "cpu"
        settings["run_on"] = "gpu"
        await engine.restart()
        assert seen[-1]["mode"] == "gpu" and engine.status()["limitSeconds"] == de.GPU_LIMIT_SECONDS
        await engine.close()

    async def test_a_model_that_reads_a_whole_scene_at_once_gets_the_bigger_batches_it_needs_and_the_other_doesnt(self, tmp_path):
        import dataclasses
        engine, settings, _ = make_engine(tmp_path)
        engine.models = {**engine.models, "laya": dataclasses.replace(engine.models["laya"], extra_args=("-b", "4096", "-ub", "4096"))}
        settings["model"] = "laya"
        await (engine.start_install()).task
        assert engine.last_command[-4:] == ["-b", "4096", "-ub", "4096"]
        await engine.close()
        settings["model"] = "kev-4b"
        await (engine.start_install()).task
        assert "-ub" not in engine.last_command
        await engine.close()
        assert de.MODELS["laya"].extra_args == ("-b", "4096", "-ub", "4096"), "measured: without them a scene over about 1,500 characters is refused"
        assert de.MODELS["kev-4b"].extra_args == (), "bigger batches took 1.8x the graphics memory for nothing on the causal model"

    async def test_it_listens_to_this_pc_on_its_own_port_range_and_the_warm_up_is_a_real_answer(self, tmp_path):
        engine, _, _ = await installed_engine(tmp_path)
        assert 29100 <= engine.port <= 29149
        s = engine.status()
        assert s["firstAnswerMs"] is not None and engine.warm_ms > 0
        assert "Ready after" in engine.log_file.read_text(encoding="utf-8")
        await engine.close()

    async def test_an_engine_that_doesnt_answer_in_System_Ones_shape_does_not_become_ready(self, tmp_path, knobs):
        knobs(FAKE_BAD_SHAPE=1)
        engine, _, _ = make_engine(tmp_path)
        job = engine.start_install()
        await job.task
        assert job.state == "error" and engine.status()["state"] == "error" and "first question failed" in engine.status()["detail"]
        assert not engine.status()["ready"]
        await engine.close()

    async def test_an_engine_that_dies_at_once_says_what_to_try(self, tmp_path, knobs):
        knobs(FAKE_DIE_AT_ONCE=1)
        engine, _, _ = make_engine(tmp_path)
        job = engine.start_install()
        await job.task
        s = engine.status()
        assert s["state"] == "died" and "antivirus" in s["detail"] and "Smart App Control" in s["detail"]
        await engine.close()

    async def test_start_without_the_files_is_a_plain_409(self, tmp_path):
        engine, _, _ = make_engine(tmp_path)
        with pytest.raises(de.DecisionError) as e:
            await engine.start()
        assert e.value.status == 409 and "isn't installed" in e.value.message

    async def test_a_leftover_engine_from_a_crashed_run_is_looked_for_and_the_pid_file_removed(self, tmp_path, monkeypatch):
        engine, _, _ = make_engine(tmp_path)
        engine.folder.mkdir(parents=True)
        engine.pid_file.write_text("99999999")
        killed = []
        monkeypatch.setattr(de, "_pid_is_llama_server", lambda pid: True)
        monkeypatch.setattr(de, "kill_tree_sync", lambda pid: killed.append(pid))
        engine.cleanup_leftover()
        assert killed == [99999999] and not engine.pid_file.exists()


# ---- answering ----------------------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestAnswer:
    async def test_the_three_kinds_come_back_shaped_like_typesafes(self, tmp_path):
        engine, _, _ = await installed_engine(tmp_path)
        res = await engine.answer(STATE, {"combat": NOUL, "mood": CHOICE, "urgency": SCORE})
        a = res["answers"]
        assert set(a) == {"combat", "mood", "urgency"}
        assert a["combat"]["type"] == "noul" and 0 <= a["combat"]["noul"] <= 1 and set(a["combat"]) == {"type", "noul"}
        assert a["mood"]["type"] == "choice" and set(a["mood"]) == {"type", "choice", "confidence", "probabilities"}
        assert set(a["mood"]["probabilities"]) == {"calm", "tense", "angry"} and sum(a["mood"]["probabilities"].values()) == pytest.approx(1.0, abs=1e-9)
        assert a["urgency"]["type"] == "score" and a["urgency"]["legend"] == {"0": "none", "1": "low", "2": "high"} and set(a["urgency"]) == {"type", "score", "confidence", "legend", "probabilities"}
        assert res["usage"]["output_tokens"] == 0 and res["usage"]["input_tokens"] > 0
        await engine.close()

    async def test_a_state_can_be_a_string_or_an_object(self, tmp_path):
        engine, _, _ = await installed_engine(tmp_path)
        assert (await engine.answer({"actor": "Lydia", "hp": 40}, {"q": NOUL}))["answers"]["q"]["type"] == "noul"
        assert (await engine.answer("plain text", {"q": NOUL}))["answers"]["q"]["type"] == "noul"
        await engine.close()

    async def test_the_question_goes_to_the_engine_as_it_is_written_and_the_engines_answer_comes_back_as_it_is(self, tmp_path, knobs):
        log = tmp_path / "log.jsonl"
        knobs(FAKE_LOG=log, FAKE_PICK="second")
        engine, _, _ = await installed_engine(tmp_path)
        log.write_text("")                                                            # forget the warm-up
        a = (await engine.answer(STATE, {"mood": CHOICE}))["answers"]["mood"]
        assert a["choice"] == "tense" and a["probabilities"]["tense"] == pytest.approx(0.7), "nothing is averaged, reordered or reshaped on the way back"
        rows = [json.loads(x) for x in log.read_text().splitlines()]
        assert len(rows) == 1 and rows[0]["ids"] == ["mood"] and rows[0]["types"] == ["choice"] and rows[0]["state"].startswith("Lydia drew"), "one call, the whole request"
        await engine.close()

    async def test_several_requests_at_once_are_all_passed_on_to_the_engine_and_counted(self, tmp_path, knobs):
        knobs(FAKE_DELAY=0.05)
        engine, settings, _ = await installed_engine(tmp_path, slots=4)
        settings["limit_seconds"] = 5
        res = await asyncio.gather(*(engine.answer(STATE + f" scene {i}", {"a": NOUL, "b": CHOICE, "c": SCORE}) for i in range(6)))
        assert all(set(r["answers"]) == {"a", "b", "c"} for r in res)
        assert engine.status()["answered"] == 6 and engine.status()["waiting"] == 0
        await engine.close()

    async def test_a_question_the_engine_cant_take_is_passed_on_with_its_reason_and_the_next_decision_is_fine(self, tmp_path, knobs):
        knobs(FAKE_REFUSE="big")
        engine, _, _ = await installed_engine(tmp_path)
        with pytest.raises(de.Unanswerable) as e:
            await engine.answer(STATE, {"ok": NOUL, "big": CHOICE})
        assert "couldn't answer this question" in e.value.reason and "'big' is not valid" in e.value.reason
        assert engine.status()["passedOn"] == 1 and engine.status()["waiting"] == 0
        assert (await engine.answer(STATE, {"ok": NOUL}))["answers"]["ok"]["type"] == "noul"
        await engine.close()

    @pytest.mark.parametrize("questions", [{}, None, "text"])
    async def test_a_request_without_questions_is_passed_on_before_the_engine_is_bothered(self, tmp_path, questions):
        engine, _, _ = await installed_engine(tmp_path)
        with pytest.raises(de.Unanswerable):
            await engine.answer(STATE, questions)
        await engine.close()

    async def test_asking_before_it_is_running_is_not_ready(self, tmp_path):
        engine, _, _ = make_engine(tmp_path)
        with pytest.raises(de.DecisionNotReady):
            await engine.answer(STATE, {"q": NOUL})
        engine, _, _ = await installed_engine(tmp_path / "b")
        await engine.stop()
        with pytest.raises(de.DecisionNotReady):
            await engine.answer(STATE, {"q": NOUL})
        assert engine.status()["state"] == "stopped"
        await engine.close()

    async def test_the_engine_answering_with_an_error_is_a_plain_error_and_the_next_decision_is_fine(self, tmp_path):
        engine, _, _ = await installed_engine(tmp_path)

        async def boom(*a, **k):
            raise de.DecisionError(502, "The decision engine returned an error (500).")
        original = engine._ask
        engine._ask = boom
        with pytest.raises(de.DecisionError):
            await engine.answer(STATE, {"q": NOUL})
        engine._ask = original
        assert (await engine.answer(STATE, {"q": NOUL}))["answers"]["q"]["type"] == "noul"
        await engine.close()


# ---- the waiting limit ----------------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestLimit:
    async def test_a_decision_not_answered_in_time_is_abandoned_at_once_and_counted(self, tmp_path, knobs):
        knobs(FAKE_DELAY=0.4)
        engine, settings, _ = await installed_engine(tmp_path)
        settings["limit_seconds"] = 0.25
        t0 = time.perf_counter()
        with pytest.raises(de.DecisionTooSlow) as e:
            await engine.answer(STATE, {"q": NOUL})
        assert time.perf_counter() - t0 < 0.35 and e.value.limit == 0.25 and "0.25 seconds" in e.value.message
        s = engine.status()
        assert s["limitHits"] == 1 and s["waiting"] == 0 and s["limitSeconds"] == 0.25
        assert "wasn't answered within 0.25 s and was passed on (1 so far)" in engine.log_file.read_text(encoding="utf-8")
        settings["limit_seconds"] = 5
        assert (await engine.answer(STATE, {"q": NOUL}))["answers"]["q"]["type"] == "noul"
        await engine.close()

    async def test_a_burst_gets_answers_while_the_waiting_ones_are_abandoned(self, tmp_path, knobs):
        knobs(FAKE_DELAY=0.1)
        engine, settings, _ = await installed_engine(tmp_path, slots=2)
        settings["limit_seconds"] = 0.35
        results = await asyncio.gather(*(engine.answer(STATE + f" scene {i}", {"a": NOUL, "b": SCORE}) for i in range(8)), return_exceptions=True)
        ok = [r for r in results if isinstance(r, dict)]
        slow = [r for r in results if isinstance(r, de.DecisionTooSlow)]
        assert ok and slow and len(ok) + len(slow) == 8
        assert engine.status()["limitHits"] == len(slow) and engine.status()["waiting"] == 0
        await engine.close()

    async def test_the_status_says_how_many_are_waiting(self, tmp_path, knobs):
        knobs(FAKE_DELAY=0.3)
        engine, settings, _ = await installed_engine(tmp_path)
        settings["limit_seconds"] = 5
        tasks = [asyncio.ensure_future(engine.answer(STATE + str(i), {"q": NOUL})) for i in range(3)]
        await asyncio.sleep(0.1)
        s = engine.status()
        assert s["waiting"] == 3 and s["label"] == "Busy (3 decisions waiting)"
        await asyncio.gather(*tasks)
        assert engine.status()["label"] == "Running" and engine.status()["answered"] == 3 and engine.status()["lastAnswerMs"] is not None
        await engine.close()


# ---- stop, restart, uninstall -------------------------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
class TestStopAndUninstall:
    async def test_stop_and_start_again(self, tmp_path):
        engine, _, _ = await installed_engine(tmp_path)
        pid = engine.proc.pid
        await engine.stop()
        assert engine.status()["state"] == "stopped" and engine.proc is None and not engine.pid_file.exists()
        await engine.start()
        assert engine.status()["state"] == "ready" and engine.proc.pid != pid
        await engine.restart()
        assert engine.status()["ready"]
        await engine.close()

    async def test_uninstall_removes_the_models_keeps_the_downloads_and_says_what_it_freed(self, tmp_path):
        engine, _, _ = await installed_engine(tmp_path)
        freed = await engine.uninstall()
        assert freed >= 0 and not (tmp_path / "decisions" / "kev-4b").exists() and not engine.state_file.exists()
        assert engine.status()["state"] == "not_installed" and engine.status()["downloads"]["files"], "the kept downloads are cleared separately"

    async def test_the_log_is_masked(self, tmp_path):
        engine, _, _ = make_engine(tmp_path)
        engine.redact = lambda s: s.replace("sk-secret", "***")
        engine.log("key sk-secret here")
        assert "sk-secret" not in engine.log_tail() and "***" in engine.log_tail()


# ---- a model name that isn't one of ours ------------------------------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_a_chosen_model_name_that_is_not_one_of_ours_falls_back_to_the_default(tmp_path):
    engine, _, _ = make_engine(tmp_path, settings={"model": "2b"})
    assert engine.model_key() == de.DEFAULT_MODEL



# ---- an engine older than llama.cpp's own decision API is fetched again ----------------------------------------------------------------------
@pytest.mark.asyncio
async def test_the_courier_asks_for_the_engine_again_when_the_one_it_has_is_too_old_and_keeps_its_kind(tmp_path):
    import decision_courier

    class Local:
        def __init__(self, current):
            self.current, self.kind = current, "vulkan"

        def engine_exe(self):
            return tmp_path / "old-llama-server"

        def engine_exe_current(self):
            return tmp_path / "llama-server" if self.current else None

        def _cfg(self):
            return {"engine": {"kind": self.kind, "build": "b11303"}}

        async def default_engine_kind(self):
            return "cuda-13.4"

    class Settings:
        data: dict = {}

        def save(self):
            pass
    from voice_downloads import DownloadStore
    cd = decision_courier.CourierDecisions(lambda: tmp_path / "d", Settings(), lambda m: None, Local(False), store=DownloadStore(lambda: tmp_path / "dl"))
    assert await cd.engine_kind_needed() == "vulkan", "the same kind it had, not a fresh guess"
    cd2 = decision_courier.CourierDecisions(lambda: tmp_path / "d2", Settings(), lambda m: None, Local(True), store=DownloadStore(lambda: tmp_path / "dl2"))
    assert await cd2.engine_kind_needed() is None


# ---- starting never stops the copy that is running; a stop MultiProxy caused is not "unexpected" (2026-10-07) ----------------------------------------------------------
import os
import subprocess
import sys
import time


def _sleeper():
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])


@pytest.mark.asyncio
class TestOneEngineOneOwner:
    async def test_starting_when_it_is_already_running_does_nothing(self, tmp_path):
        engine, _, _ = await installed_engine(tmp_path)
        seen = count_launches(engine)
        pid = engine.proc.pid
        await engine.start()
        await engine.start()
        assert engine.proc.pid == pid and engine.status()["state"] == "ready" and seen == [], "no second engine, nothing stopped"
        await engine.close()

    async def test_the_pid_file_names_the_engine_and_the_process_that_started_it(self, tmp_path):
        engine, _, _ = await installed_engine(tmp_path)
        import json
        d = json.loads(engine.pid_file.read_text())
        assert d["engine"] == engine.proc.pid and d["owner"] == os.getpid() and d["token"] == de._process_token(os.getpid()) != ""
        await engine.close()

    async def test_the_engine_this_process_started_is_never_a_leftover(self, tmp_path, monkeypatch):
        engine, _, _ = await installed_engine(tmp_path)
        killed = []
        monkeypatch.setattr(de, "kill_tree_sync", lambda pid: killed.append(pid))
        monkeypatch.setattr(de, "_pid_is_llama_server", lambda pid: True)
        engine.cleanup_leftover()
        assert killed == [] and engine.proc.poll() is None and engine.pid_file.exists() and engine.status()["state"] == "ready"
        await engine.close()

    async def test_the_engine_of_another_running_multiproxy_is_left_alone(self, tmp_path, monkeypatch):
        engine, _, _ = make_engine(tmp_path)
        engine.folder.mkdir(parents=True)
        other = _sleeper()
        try:
            import json
            engine.pid_file.write_text(json.dumps({"engine": 99999999, "owner": other.pid, "token": de._process_token(other.pid)}))
            killed = []
            monkeypatch.setattr(de, "kill_tree_sync", lambda pid: killed.append(pid))
            monkeypatch.setattr(de, "_pid_is_llama_server", lambda pid: True)
            engine.cleanup_leftover()
            assert killed == [] and engine.pid_file.exists()
            assert any("Left alone: the decision engine (process ID 99999999) belongs to another MultiProxy that is still running" in l for l in engine.log_tail().splitlines())
        finally:
            other.kill()
            other.wait()

    async def test_the_engine_of_an_owner_that_is_gone_is_a_leftover_stopped_by_its_own_id_only(self, tmp_path, monkeypatch):
        engine, _, _ = make_engine(tmp_path)
        engine.folder.mkdir(parents=True)
        gone = _sleeper()
        token = de._process_token(gone.pid)
        gone.kill()
        gone.wait()
        import json
        engine.pid_file.write_text(json.dumps({"engine": 99999999, "owner": gone.pid, "token": token}))
        killed = []
        monkeypatch.setattr(de, "kill_tree_sync", lambda pid: killed.append(pid))
        monkeypatch.setattr(de, "_pid_is_llama_server", lambda pid: True)
        engine.cleanup_leftover()
        assert killed == [99999999] and not engine.pid_file.exists()
        assert any("Stopped (leftover from before): a decision engine from an earlier run (process ID 99999999)" in l for l in engine.log_tail().splitlines())

    async def test_a_reused_process_number_is_not_mistaken_for_the_owner(self, tmp_path, monkeypatch):
        engine, _, _ = make_engine(tmp_path)
        engine.folder.mkdir(parents=True)
        other = _sleeper()
        try:
            import json
            engine.pid_file.write_text(json.dumps({"engine": 99999999, "owner": other.pid, "token": "an-older-process-with-the-same-number"}))
            killed = []
            monkeypatch.setattr(de, "kill_tree_sync", lambda pid: killed.append(pid))
            monkeypatch.setattr(de, "_pid_is_llama_server", lambda pid: True)
            engine.cleanup_leftover()
            assert killed == [99999999], "the number belongs to a different process now: the old owner is gone"
        finally:
            other.kill()
            other.wait()

    async def test_stopping_never_removes_another_processs_pid_file(self, tmp_path):
        engine, _, _ = await installed_engine(tmp_path)
        other = _sleeper()
        try:
            import json
            engine.pid_file.write_text(json.dumps({"engine": 1, "owner": other.pid, "token": de._process_token(other.pid)}))
            await engine.stop()
            assert engine.pid_file.exists()
        finally:
            other.kill()
            other.wait()
            await engine.close()


@pytest.mark.asyncio
class TestAStopMultiProxyCausedIsNamedAsWhatItWas:
    async def test_a_restart_stops_then_starts_and_says_so_in_that_order(self, tmp_path):
        engine, _, _ = await installed_engine(tmp_path)
        pid = engine.proc.pid
        await engine.restart()
        lines = engine.log_tail().splitlines()
        i_stop = max(i for i, l in enumerate(lines) if "Stopped (restart)" in l)
        i_start = max(i for i, l in enumerate(lines) if "Starting on port" in l)
        assert i_stop < i_start and engine.proc.pid != pid and engine.status()["state"] == "ready"
        assert engine.status()["detail"] == "" and "unexpectedly" not in engine.log_tail()
        await engine.close()

    async def test_a_plain_stop_is_just_stopped(self, tmp_path):
        engine, _, _ = await installed_engine(tmp_path)
        await engine.stop()
        assert engine.status()["state"] == "stopped" and "unexpectedly" not in engine.log_tail()
        await engine.close()

    async def test_an_engine_that_dies_on_its_own_is_still_called_unexpected(self, tmp_path):
        engine, _, _ = await installed_engine(tmp_path)
        engine.started_at = time.time() - 100
        engine.proc.kill()
        engine.proc.wait()
        with pytest.raises(de.DecisionNotReady):
            await engine.answer("x", {"q": {"type": "noul", "instructions": "?"}})
        s = engine.status()
        assert s["state"] == "died" and "The model stopped unexpectedly" in s["detail"]
        await engine.close()
