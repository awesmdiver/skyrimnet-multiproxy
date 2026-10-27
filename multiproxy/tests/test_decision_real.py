"""The decision engine against the REAL pinned model and the real llama.cpp engine, when they are on this device (the step 0 scratch folder, or the folder named by
DECISION_MODEL_DIR; skipped everywhere else). It runs on system memory (never on the card, so it never competes with a game). It pins what the measurements say:
the recommended model's answers on the two test sets, and that llama.cpp's own decision API takes MultiProxy's questions straight through.

Folder layout expected: <dir>/dl/<the pinned model file, under its pinned name> and <dir>/engine/cpu/llama-server.exe (or `llama-server`)."""
import asyncio
import json
import os
import sys
from pathlib import Path

import pytest
import pytest_asyncio

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import decision_check as dc            # noqa: E402
import decision_engine as de           # noqa: E402
from voice_downloads import DownloadStore, FolderSource   # noqa: E402

DIR = Path(os.environ.get("DECISION_MODEL_DIR") or r"D:\scratch\nat")
FILES = DIR / "dl"
EXE = next((p for p in (DIR / "engine" / "cpu" / "llama-server.exe", DIR / "llama-server.exe", DIR / "llama-server") if p.exists()), None)
KEY = de.DEFAULT_MODEL
HAVE = (FILES / de.MODELS[KEY].model.name).exists() and EXE is not None
pytestmark = [pytest.mark.skipif(not HAVE, reason="the real model file and llama-server aren't on this device"), pytest.mark.asyncio(loop_scope="module")]


def run_cases(rows_of):
    by_cat: dict = {}
    for r in rows_of:
        by_cat.setdefault(r["category"], [0, 0])
        by_cat[r["category"]][0] += r["ok"]
        by_cat[r["category"]][1] += 1
    return sum(r["ok"] for r in rows_of), len(rows_of), by_cat


async def run_set(eng, cases: list) -> list:
    rows = []
    for c in cases:
        a = (await eng.answer(c["state"], {"q": c["question"]}))["answers"]["q"]
        ok, pc = dc.judge(c, a)
        rows.append({"id": c["id"], "category": c["category"], "ok": ok, "pc": pc})
    return rows


@pytest_asyncio.fixture(scope="module", loop_scope="module")
async def engine(tmp_path_factory):
    work = tmp_path_factory.mktemp("real-decisions")
    store = DownloadStore(lambda: work / "downloads")
    settings = {"model": KEY, "run_on": "cpu", "limit_seconds": 60}

    async def ensure(_progress):
        return EXE
    eng = de.DecisionEngine(lambda: work / "decisions", store, lambda: settings, lambda: EXE, ensure, lambda: "cpu", port_range=(29200, 29249),
                            source_fn=lambda spec: FolderSource(FILES))
    job = eng.start_install()
    await job.task
    assert job.state == "done", job.error
    yield eng
    await eng.close()


async def test_it_started_on_system_memory_through_the_products_own_install_and_said_so(engine):
    s = engine.status()
    assert s["ready"] and s["runOn"] == "cpu" and s["runOnLabel"] == "System memory" and s["firstAnswerMs"] and s["model"] == KEY


async def test_the_easy_set_scores_as_measured(engine):
    cases = json.loads(dc.SETS["easy"].read_text(encoding="utf-8"))["cases"]
    got, total, _ = run_cases(await run_set(engine, cases))
    assert total == 52 and got >= 50, f"measured 51/52, the earlier Jevify 2B model 46/52: {got}/{total}"


async def test_the_harder_set_scores_and_the_three_or_more_people_group_has_its_own_line(engine):
    cases = json.loads(dc.SETS["hard"].read_text(encoding="utf-8"))["cases"]
    got, total, cats = run_cases(await run_set(engine, cases))
    assert total == 44 and got >= 40, f"measured 42/44, the earlier model 41/44: {got}/{total}"
    who = cats["addressed3"]
    assert who[1] == 12 and who[0] >= 11, f"who is the player talking to: {who[0]}/{who[1]}"


async def test_a_six_question_request_and_a_fifteen_option_question_go_straight_through(engine):
    state, six = dc.six_questions()
    res = await engine.answer(state, six)
    assert set(res["answers"]) == set(six) and res["usage"]["input_tokens"] > 0 and res["usage"]["output_tokens"] == 0
    assert 0 <= res["answers"]["combat"]["noul"] <= 1 and sum(res["answers"]["mood"]["probabilities"].values()) == pytest.approx(1.0, abs=1e-3)
    mood = {"mood": {"type": "choice", "instructions": "What is Lydia's most likely mood?", "criteria": {m: None for m in dc.MOODS}}}
    a = (await engine.answer(state, mood))["answers"]["mood"]
    assert len(a["probabilities"]) == 15 and a["choice"] in dc.MOODS


async def test_a_question_the_real_engine_refuses_is_passed_on_and_the_next_one_is_answered(engine):
    bad = {"type": "choice", "instructions": "who", "criteria": {}}
    with pytest.raises(de.Unanswerable):
        await engine.answer("Anything.", {"q": bad})
    assert (await engine.answer("The guard waves.", {"q": {"type": "noul", "instructions": "Is someone waving?"}}))["answers"]["q"]["noul"] > 0.5


async def test_the_longest_state_the_context_is_sized_for_is_answered(engine):
    filler = "Earlier that evening Lydia had told Serana about the bandits at Halted Stream Camp and how the guards in Whiterun had refused to help. "
    state = (filler * 90)[:11500] + " Right now Lydia is sharpening her sword by the fire."
    a = (await engine.answer(state, {"q": {"type": "noul", "instructions": "Is Lydia holding a weapon?"}}))["answers"]["q"]
    assert 0 <= a["noul"] <= 1


async def test_a_255_option_question_is_answered_and_a_256_option_one_is_passed_on(engine):
    state = "Lydia is sharpening her sword by the fire."
    big = {"type": "choice", "instructions": "Which name did she hear?", "criteria": {f"person {i}": None for i in range(255)}}
    assert len((await engine.answer(state, {"q": big}))["answers"]["q"]["probabilities"]) == 255, "the earlier model passed on anything over 26"
    big["criteria"]["one too many"] = None
    with pytest.raises(de.Unanswerable) as e:
        await engine.answer(state, {"q": big})
    assert "at most 255" in e.value.reason
