"""The Local models list says what is true about each model (2026-10-07): Downloaded, Update available, Already in Ollama / LM Studio, or Download.

Files here are tiny stand-ins with a made-up catalog entry; nothing big is written. Ollama and LM Studio are stand-in lists, never the real programs.
"""
import hashlib
import json

import pytest

import local_models as lm
import model_matches

PAYLOAD = b"x" * 1000
MM = b"y" * 100
SHA = hashlib.sha256(PAYLOAD).hexdigest()
MM_SHA = hashlib.sha256(MM).hexdigest()
SPEC = {"id": "gemma-4-26b-a4b", "name": "Gemma 4 26B A4B", "blurb": "", "vision": True, "repo": "r", "files": [
    {"file": "m.gguf", "size": len(PAYLOAD), "sha256": SHA, "role": "model"},
    {"file": "mm.gguf", "size": len(MM), "sha256": MM_SHA, "role": "mmproj"}]}


def _put(tmp_path, main=PAYLOAD, mm=MM, notes=True):
    d = tmp_path / "models" / SPEC["id"]
    d.mkdir(parents=True)
    (d / "m.gguf").write_bytes(main)
    (d / "mm.gguf").write_bytes(mm)
    if notes:
        lm.remember_verified(d / "m.gguf", hashlib.sha256(main).hexdigest())
        lm.remember_verified(d / "mm.gguf", hashlib.sha256(mm).hexdigest())
    return d


# ---- on disk ----

def test_a_pinned_file_in_the_folder_is_downloaded(tmp_path):
    _put(tmp_path)
    assert lm.model_disk_state(tmp_path, SPEC) == "ok"


def test_nothing_there_or_no_folder_is_none(tmp_path):
    assert lm.model_disk_state(tmp_path, SPEC) == "none" and lm.model_disk_state(None, SPEC) == "none"


def test_a_partial_or_damaged_file_is_not_downloaded(tmp_path):
    d = _put(tmp_path, notes=False)
    (d / "m.gguf").write_bytes(PAYLOAD[:-5])                  # cut short, nothing vouches for it
    assert lm.model_disk_state(tmp_path, SPEC) == "none"
    (d / "m.gguf").write_bytes(PAYLOAD)
    (d / "mm.gguf").unlink()
    assert lm.model_disk_state(tmp_path, SPEC) == "none", "the vision file is missing"


def test_an_older_pinned_version_is_an_update_and_stays_on_disk(tmp_path):
    old = b"z" * 900                                          # a complete file that was checked when downloaded, of another pinned version
    d = _put(tmp_path, main=old)
    assert lm.model_disk_state(tmp_path, SPEC) == "update"
    assert (d / "m.gguf").read_bytes() == old, "asking never touches the old file"
    d2 = tmp_path / "again"
    (d2 / "models" / SPEC["id"]).mkdir(parents=True)
    same_size_other_sum = b"q" * len(PAYLOAD)                  # same size, other checksum: also another version
    p = d2 / "models" / SPEC["id"]
    (p / "m.gguf").write_bytes(same_size_other_sum)
    (p / "mm.gguf").write_bytes(MM)
    lm.remember_verified(p / "m.gguf", hashlib.sha256(same_size_other_sum).hexdigest())
    lm.remember_verified(p / "mm.gguf", MM_SHA)
    assert lm.model_disk_state(d2, SPEC) == "update"


# ---- in another program ----

def _tag(name, family, params, size):
    return {"name": name, "size": size, "details": {"family": family, "parameter_size": params}}


REAL = {"id": "gemma-4-26b-a4b", "files": [{"role": "model", "size": 16796017248}, {"role": "mmproj", "size": 1194827744}]}


def test_the_same_model_in_ollama_is_matched_by_identity_and_size():
    assert model_matches.match_ollama(REAL, [_tag("gemma4:26b", "gemma4", "25.8B", 18_600_000_000)]) == "gemma4:26b"


def test_a_different_model_of_similar_size_is_not_matched():
    assert model_matches.match_ollama(REAL, [_tag("gemma3:27b", "gemma3", "27B", 18_000_000_000)]) is None, "another family"
    assert model_matches.match_ollama(REAL, [_tag("gemma4:31b", "gemma4", "31B", 18_300_000_000)]) is None, "another size in billions"
    assert model_matches.match_ollama(REAL, [_tag("gemma4:26b-q8", "gemma4", "25.8B", 28_000_000_000)]) is None, "another file size (a different quantization)"
    assert model_matches.match_ollama(REAL, [{"name": "gemma4:26b", "size": 18_600_000_000}]) is None, "no details: never a guess from the name alone"


def test_an_ollama_that_could_not_be_read_says_nothing():
    assert model_matches.match_ollama(REAL, None) is None and model_matches.match_ollama(REAL, []) is None


def test_lm_studio_matches_a_file_by_name_and_size_and_names_its_id_only_when_clear():
    f = {"name": "gemma-4-26B-A4B-it-Q4_K_M", "size_bytes": 17_990_000_000}
    assert model_matches.match_lmstudio(REAL, [f]) == f
    assert model_matches.match_lmstudio(REAL, [{**f, "name": "gemma-4-31B-it-Q4_K_M"}]) is None
    assert model_matches.match_lmstudio(REAL, [{**f, "size_bytes": 5_000_000}]) is None
    assert model_matches.lmstudio_id(REAL, ["google/gemma-4-26b-a4b", "qwen/qwen3-4b"]) == "google/gemma-4-26b-a4b"
    assert model_matches.lmstudio_id(REAL, ["a/gemma-4-26b", "b/gemma-4-26b-a4b"]) is None, "two candidates: no guess"


@pytest.mark.asyncio
async def test_the_page_answer_carries_the_other_program_and_keeps_download_when_unreachable(proxy_module, monkeypatch):
    spec = lm.CATALOG_BY_ID["gemma-4-26b-a4b"]
    tags = {"models": [_tag("gemma4:26b", "gemma4", "25.8B", 18_600_000_000)]}

    async def fake_tags(*a, **k):
        return tags
    monkeypatch.setattr(proxy_module, "_fetch_ollama_tags", fake_tags)
    monkeypatch.setattr(proxy_module.courier_adapters, "lm_models_dir", lambda: None)
    got = await proxy_module._models_elsewhere([spec])
    assert got == {"gemma-4-26b-a4b": {"program": "Ollama", "name": "gemma4:26b", "useName": "ollama/gemma4:26b"}}

    async def down(*a, **k):
        return None
    monkeypatch.setattr(proxy_module, "_fetch_ollama_tags", down)
    assert await proxy_module._models_elsewhere([spec]) == {}, "Ollama not running: nothing extra"


def test_the_route_survives_a_broken_finder(proxy_module, test_client, monkeypatch):
    async def boom(specs):
        raise RuntimeError("x")
    monkeypatch.setattr(proxy_module, "_models_elsewhere", boom)
    snap = test_client.get("/config/local-models").json()
    assert all(c["elsewhere"] is None for c in snap["catalog"]) and all("update" in c for c in snap["catalog"])


# ---- the page's words and the engine note ----

def test_the_page_has_the_new_words_and_asks_about_the_engine_each_time(test_client):
    page = test_client.get("/").text
    for words in ("Update available", "Already in ", "Use it", "downloaded: 'Downloaded'", "lmEngUpdateAsked = false", "lmBuildNumber(upd.build) <= lmBuildNumber(s.engine.build)", "lmOpened();"):
        assert words in page, words
    assert "30 * 60 * 1000" not in page, "the page no longer waits half an hour to ask about the engine"
