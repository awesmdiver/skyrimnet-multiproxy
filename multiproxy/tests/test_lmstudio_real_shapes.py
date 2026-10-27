"""LM Studio's command-line tool, as the REAL one printed it (captured 2026-10-01 from an installed LM Studio on the dev device; the
`lms` there is `~/.lmstudio/bin/lms.exe`, `lms --version` prints only a CLI commit, `69d945a`). The stand-in tests in test_courier.py
keep exercising every action; these check the parser and the adapter against output that wasn't written by us."""
import asyncio
import json
import logging

import pytest

import courier_adapters as ca

# `lms ls --json`: a banner line, then one JSON list (two language models and an embedding model)
REAL_LS = ('Waking up LM Studio service...\n[{"type":"llm","modelKey":"google/gemma-4-12b-qat","format":"gguf","displayName":"Gemma 4 12B QAT","publisher":"google",'
           '"path":"google/gemma-4-12b-qat","sizeBytes":7151067268,"indexedModelIdentifier":"google/gemma-4-12b-qat","deviceIdentifier":null,"paramsString":"12B",'
           '"architecture":"gemma4","quantization":{"name":"Q4_0","bits":4},"variants":["google/gemma-4-12b-qat@q4_0"],"selectedVariant":"google/gemma-4-12b-qat@q4_0",'
           '"vision":true,"trainedForToolUse":true,"maxContextLength":262144},{"type":"llm","modelKey":"google/gemma-4-e4b","format":"gguf","displayName":"Gemma 4 E4B",'
           '"publisher":"google","path":"google/gemma-4-e4b","sizeBytes":5335366779,"indexedModelIdentifier":"google/gemma-4-e4b","deviceIdentifier":null,'
           '"paramsString":"7.5B","architecture":"gemma4","quantization":{"name":"Q4_K_M","bits":4},"variants":["google/gemma-4-e4b@q4_k_m"],'
           '"selectedVariant":"google/gemma-4-e4b@q4_k_m","vision":false,"trainedForToolUse":true,"maxContextLength":131072},{"type":"embedding",'
           '"modelKey":"text-embedding-nomic-embed-text-v1.5","format":"gguf","displayName":"Nomic Embed Text v1.5","publisher":"nomic-ai",'
           '"path":"nomic-ai/nomic-embed-text-v1.5-GGUF/nomic-embed-text-v1.5.Q4_K_M.gguf","sizeBytes":84106624,'
           '"indexedModelIdentifier":"nomic-ai/nomic-embed-text-v1.5-GGUF/nomic-embed-text-v1.5.Q4_K_M.gguf","deviceIdentifier":null,"architecture":"nomic-bert",'
           '"quantization":{"name":"Q4_K_M","bits":4},"maxContextLength":2048}]\n')
REAL_PS_EMPTY = "[]\n"
REAL_VERSION = ("   __   __  ___  ______          ___        _______   ____\n  / /  /  |/  / / __/ /___ _____/ (_)__    / ___/ /  /  _/\n"
                " / /__/ /|_/ / _\\ \\/ __/ // / _  / / _ \\  / /__/ /___/ /  \n/____/_/  /_/ /___/\\__/\\_,_/\\_,_/_/\\___/  \\___/____/___/  \n\n"
                "lms is LM Studio's CLI utility for your models, server, and inference runtime.\nCLI commit: 69d945a\n\nDocs: https://lmstudio.ai/docs/developer\n"
                "Join our Discord: https://discord.gg/lmstudio\nContribute: https://github.com/lmstudio-ai/lms\n")
log = logging.getLogger("lms-real")


def adapter(ps=REAL_PS_EMPTY):
    async def runner(args, timeout=60):
        if args[:1] == ["ls"]:
            return 0, REAL_LS, ""
        if args[:1] == ["ps"]:
            return 0, ps, ""
        if args[:1] == ["version"]:
            return 0, REAL_VERSION, ""
        return 1, "", "unexpected"
    return ca.LmStudioAdapter(log, base="http://127.0.0.1:1", cli="lms", runner=runner)


def test_the_real_ls_output_with_its_banner_line_parses():
    rows = ca.parse_lms_json(REAL_LS)
    assert [r["modelKey"] for r in rows] == ["google/gemma-4-12b-qat", "google/gemma-4-e4b", "text-embedding-nomic-embed-text-v1.5"]
    assert rows[0]["vision"] is True and rows[1]["vision"] is False and rows[0]["sizeBytes"] == 7151067268


def test_the_adapter_lists_the_real_models_without_the_embedding_one_and_with_vision():
    models = asyncio.run(adapter().models())
    assert [(m["id"], m["name"], m["vision"], m["loaded"], m["state"]) for m in models] == [
        ("google/gemma-4-12b-qat", "Gemma 4 12B QAT", True, False, "available"),
        ("google/gemma-4-e4b", "Gemma 4 E4B", False, False, "available")]
    assert models[0]["size_bytes"] == 7151067268 and models[0]["on_card_percent"] is None


def test_a_loaded_model_in_the_real_ps_shape_is_marked_loaded():
    ps = json.dumps([{"identifier": "google/gemma-4-e4b", "modelKey": "google/gemma-4-e4b", "type": "llm"}])
    models = asyncio.run(adapter(ps).models())
    assert {m["id"]: m["loaded"] for m in models} == {"google/gemma-4-12b-qat": False, "google/gemma-4-e4b": True}


def test_the_real_version_command_has_no_version_number_so_none_is_shown():
    probe = asyncio.run(adapter().probe())
    assert probe["installed"] is True and probe["version"] is None, "never a made-up number: `lms version` only prints a CLI commit"


def test_a_models_path_is_its_key_not_a_file_so_delete_is_unsupported():
    m = asyncio.run(adapter().models())[0]
    assert m["_path"] == m["id"] == "google/gemma-4-12b-qat"
    assert ca.LmStudioAdapter.capabilities["delete"] is False
    with pytest.raises(ca.AdapterError) as e:
        asyncio.run(adapter().delete("google/gemma-4-12b-qat"))
    assert e.value.message == "LM Studio doesn't let other programs delete models. Delete it in LM Studio on that device."


def test_the_install_cli_message_is_the_one_real_go_to_that_pc_step():
    assert ca.LMS_NOT_FOUND == "LM Studio isn't fully set up. In LM Studio on that device, open Settings, click \"Install CLI\", and try again."
