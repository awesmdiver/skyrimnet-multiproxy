"""Tested settings for each job (Routing > Settings for this model, 2026-10-26): a preset applies only to its job and model family, a switch that is off changes nothing, the
person's own setting wins, an untested model shows "Not tested on this model" and does nothing, and a fixed answer format that gives an empty reply is asked again without it."""
import copy
import json

import pytest

import routing
import tested_settings as ts

ACTION_PROMPT = """## Task
You are the Skyrim GameMaster. Select an action.
## Output Format
Return exactly one line starting with `ACTION:`. No reasoning or explanations.
---
## Available Actions
- `StartConversation` PARAMS: {"speaker":"Vaela|Cybele","target":"Vaela|Cybele|Shaelle"} — Trigger an NPC to start talking.
- `ContinueConversation` PARAMS: {"speaker":"Vaela|Cybele","target":"Vaela|Cybele|Shaelle","topic":"string"} — Continue.
- `None` — No action needed
"""
MEMORY_PROMPT = 'Respond ONLY with a JSON object:\n{"content": "x", "location": "y", "emotion": "joyful", "importance_score": 0.5, "tags": ["a"], "type": "JOY"}'
EXTRA = {"provider": {"order": ["x"]}, "top_p": 0.9}


def run(kind, model, engine, switches, extra=None, text=None, streamed=False, own=None, built_in=None):
    text = text if text is not None else (ACTION_PROMPT if kind == "action" else MEMORY_PROMPT if kind == "memory" else "hello")
    return ts.apply(kind, ts.family_of(model), engine, switches, engine == "builtin" if built_in is None else built_in, EXTRA if extra is None else extra,
                    prompt_text=text, streamed=streamed, own=own or {})


class TestWhichModel:
    def test_families(self):
        assert ts.family_of("local/gemma-4-12b") == "gemma4" == ts.family_of("ollama/gemma4:e2b")
        assert ts.family_of("local/qwen3.5-0.8b") == "qwen35"
        assert ts.family_of("local/llama-3-8b") is None and ts.family_of("openai/gpt-5") is None

    def test_engines(self):
        assert ts.engine_of("local") == "builtin" and ts.engine_of("ollama") == "ollama" and ts.engine_of("lmstudio") == "lmstudio"
        assert ts.engine_of("remote", "llamacpp") == "llamacpp" and ts.engine_of("remote", "llama.cpp") == "llamacpp"
        assert ts.engine_of("remote", "MultiProxy") is None and ts.engine_of("openai") is None and ts.engine_of("glm") is None

    def test_jobs(self):
        assert ts.job_kind(["gamemaster_evaluation"]) == "action" and ts.job_kind(["memory"]) == "memory" and ts.job_kind(["dialogue"]) == "dialogue"
        assert ts.job_kind(["vision"]) is None and ts.job_kind(["meta", "decisions"]) is None


class TestCard:
    def test_a_built_in_model_starts_with_the_proven_settings_on(self):
        c = ts.card("memory", "local/gemma-4-12b", "local", None, {})
        rows = {r["id"]: r for r in c["rows"]}
        assert rows["answer_format"]["on"] and rows["answer_format"]["tested"] and rows["answer_format"]["evidence"] == "20 of 20 (was 0 of 20)"
        assert rows["thinking_off"]["now"] == "Always (built in)"
        assert "Gemma 4 E2B" in c["testedOn"] and "b11886" in c["testedOn"] and "2026-10-25" in c["testedOn"]

    def test_any_other_model_starts_off_and_asks(self):
        c = ts.card("memory", "ollama/gemma4:e2b", "ollama", None, {})
        assert not any(r["on"] for r in c["rows"]) and not c["anyOn"]

    def test_the_person_can_turn_a_built_in_default_off(self):
        c = ts.card("memory", "local/gemma-4-12b", "local", None, {"answer_format": False})
        assert not next(r for r in c["rows"] if r["id"] == "answer_format")["on"]

    def test_an_untested_model_says_so_and_cannot_be_switched_on(self):
        c = ts.card("gamemaster_evaluation", "local/llama-3-8b", "local", None, {"answer_format": True, "thinking_off": True})
        assert not c["anyTested"] and all(r["recommended"] == ts.NOT_TESTED and not r["on"] for r in c["rows"])
        assert [r["id"] for r in c["rows"]] == ["answer_format", "thinking_off"]          # (no steady-temperature row where nothing found it helps)

    def test_lm_studio_is_not_tested(self):
        c = ts.card("memory", "lmstudio/gemma-4-12b", "lmstudio", None, {"answer_format": True})
        assert not c["anyTested"] or not next(r for r in c["rows"] if r["id"] == "answer_format")["on"]

    def test_a_grammar_is_not_offered_for_ollama(self):
        c = ts.card("gamemaster_evaluation", "ollama/gemma4:e2b", "ollama", None, {})
        assert not next(r for r in c["rows"] if r["id"] == "answer_format")["tested"]

    def test_the_steady_temperature_is_offered_only_where_it_helped(self):
        assert any(r["id"] == "temperature" for r in ts.card("gamemaster_evaluation", "local/qwen3.5-0.8b", "local", None, {})["rows"])
        assert not any(r["id"] == "temperature" for r in ts.card("gamemaster_evaluation", "local/gemma-4-12b", "local", None, {})["rows"])

    def test_a_cloud_model_and_a_job_without_settings_get_no_section(self):
        assert ts.card("memory", "openai/gpt-5", "openai", None, {}) is None
        assert ts.card("vision", "local/gemma-4-12b", "local", None, {}) is None

    def test_dialogue_has_only_thinking(self):
        assert [r["id"] for r in ts.card("dialogue", "local/gemma-4-12b", "local", None, {})["rows"]] == ["thinking_off"]


class TestApply:
    def test_memory_gets_the_schema_and_a_retry_copy_without_it(self):
        out, plain, applied = run("memory", "local/gemma-4-12b", "builtin", {})
        assert out["response_format"]["json_schema"]["schema"]["required"] == ["content", "location", "emotion", "importance_score", "tags", "type"]
        assert plain == EXTRA and applied == ["answer_format"]

    def test_off_restores_the_request_byte_for_byte(self):
        before = copy.deepcopy(EXTRA)
        out, plain, applied = run("memory", "local/gemma-4-12b", "builtin", {"answer_format": False})
        assert out == before and json.dumps(out, sort_keys=True) == json.dumps(before, sort_keys=True) and plain is None and applied == []
        assert EXTRA == before

    def test_only_its_own_job(self):
        assert run("action", "local/gemma-4-12b", "builtin", {}, text=MEMORY_PROMPT)[2] == []      # an action job with a memory prompt: the shape is wrong, nothing is added
        assert run("memory", "local/gemma-4-12b", "builtin", {}, text=ACTION_PROMPT)[2] == []
        assert run("memory", "local/gemma-4-12b", "builtin", {}, text="Respond with a haiku")[2] == []

    def test_only_its_own_model_family(self):
        assert run("memory", "local/llama-3-8b", "builtin", {}, built_in=True)[2] == []
        assert run("memory", "lmstudio/gemma-4-12b", "lmstudio", {"answer_format": True})[2] == []

    def test_a_person_s_own_setting_wins(self):
        out, _, applied = run("action", "local/qwen3.5-0.8b", "builtin", {}, own={"temperature": 0.9})
        assert "temperature" not in out and "temperature" not in applied
        out, _, _ = run("action", "local/qwen3.5-0.8b", "builtin", {})
        assert out["temperature"] == 0.3

    def test_what_the_request_already_carries_wins(self):
        mine = {"response_format": {"type": "json_object"}, "temperature": 0.1}
        out, plain, applied = run("memory", "local/gemma-4-12b", "builtin", {}, extra=mine)
        assert out == mine and applied == [] and plain is None

    def test_a_stream_gets_no_fixed_format(self):
        out, plain, applied = run("memory", "local/gemma-4-12b", "builtin", {}, streamed=True)
        assert "response_format" not in out and plain is None

    def test_thinking_is_said_skyrimnets_way_for_an_engine_that_was_not_started_here(self):
        out, plain, applied = run("action", "ollama/gemma4:e2b", "ollama", {"thinking_off": True})
        assert out["reasoning"] == {"enabled": False} and applied == ["thinking_off"] and plain is None
        assert run("dialogue", "local/gemma-4-12b", "builtin", {})[2] == []                    # the built-in engine is always started with thinking off: nothing to add
        assert run("action", "ollama/gemma4:e2b", "ollama", {"thinking_off": True}, extra={"reasoning": {"enabled": True}})[2] == []

    def test_not_tested_does_nothing_even_if_the_file_says_on(self):
        assert run("memory", "local/llama-3-8b", "builtin", {"answer_format": True, "thinking_off": True})[2] == []


class TestActionGrammar:
    def test_it_is_built_from_the_requests_own_list(self):
        g = ts.action_grammar(ACTION_PROMPT)
        assert g.startswith('root ::= "ACTION: "') and '"Vaela"' in g and '"Shaelle"' in g and '"None"' in g
        assert 'StartConversation PARAMS: ' in g and 'ContinueConversation PARAMS: ' in g

    def test_a_list_it_cannot_read_gives_nothing(self):
        assert ts.action_grammar("no actions here") is None
        assert ts.action_grammar('## Available Actions\n- `Odd` PARAMS: {"n": 5}\n') is None
        assert ts.action_grammar('## Available Actions\n- `Odd` PARAMS: {"n": "a\\"b|c"}\n') is None
        out, plain, applied = run("action", "local/gemma-4-12b", "builtin", {}, text=ACTION_PROMPT.replace('"speaker":"Vaela|Cybele"', '"speaker":5', 1))
        assert "grammar" not in out

    def test_action_gets_the_grammar_on_llama_cpp(self):
        out, plain, applied = run("action", "local/gemma-4-12b", "builtin", {})
        assert "grammar" in out and "grammar" not in plain and "answer_format" in applied
        assert "grammar" not in run("action", "ollama/gemma4:e2b", "ollama", {"answer_format": True})[0]


class TestRetry:
    def test_good_and_bad_replies(self):
        assert ts.reply_ok("action", "ACTION: None") and not ts.reply_ok("action", "") and not ts.reply_ok("action", "I think we should talk")
        assert ts.reply_ok("memory", '{"content": "x"}') and ts.reply_ok("memory", '```json\n{"a": 1}\n```') and not ts.reply_ok("memory", "no json")

    @pytest.mark.asyncio
    async def test_an_empty_reply_is_asked_again_once_without_the_format(self, proxy_module):
        calls = []

        async def direct(*args, **extra):
            calls.append(extra)
            return ("" if "response_format" in extra else '{"content": "x"}'), {}
        out = await proxy_module._ask_with_safety_net(direct, ("s", [], "m", 50), {"response_format": {"type": "json_object"}, "a": 1}, {"a": 1}, "memory", "t")
        assert out[0] == '{"content": "x"}' and calls == [{"response_format": {"type": "json_object"}, "a": 1}, {"a": 1}]

    @pytest.mark.asyncio
    async def test_a_refusal_is_asked_again_and_a_good_reply_is_not(self, proxy_module):
        from fastapi import HTTPException
        calls = []

        async def refuses(*args, **extra):
            calls.append(extra)
            if "grammar" in extra:
                raise HTTPException(status_code=400, detail="no grammar")
            return "ACTION: None", {}
        assert (await proxy_module._ask_with_safety_net(refuses, (), {"grammar": "g"}, {}, "action", "t"))[0] == "ACTION: None" and len(calls) == 2
        calls.clear()

        async def fine(*args, **extra):
            calls.append(extra)
            return "ACTION: None", {}
        await proxy_module._ask_with_safety_net(fine, (), {"grammar": "g"}, {}, "action", "t")
        assert len(calls) == 1
        calls.clear()
        await proxy_module._ask_with_safety_net(fine, (), {"x": 1}, None, None, "t")             # nothing was added: asked once, as always
        assert len(calls) == 1


class TestSettingsFile:
    def test_switches_are_kept_clean_and_saved_with_routing(self):
        s = routing.clean_settings({"tested": {"memory": {"answer_format": True, "bogus": True, "temperature": "yes"}, "bad key!": {"answer_format": True}}})
        assert s["tested"] == {"memory": {"answer_format": True}}
        assert routing.clean_settings({})["tested"] == {}

    def test_the_first_job_with_switches_is_used(self):
        assert ts.switches_for({"tested": {"b": {"answer_format": False}}}, ["a", "b"]) == {"answer_format": False}
        assert ts.switches_for({}, ["a"]) == {}

    def test_the_shipped_file_only_claims_what_has_a_count(self):
        for job in ts.presets()["jobs"].values():
            for fams in job["settings"].values():
                for engines in fams.values():
                    for e in engines.values():
                        assert 0 <= e["result"] <= e["of"] and 0 <= e["without"] <= e["of"]
