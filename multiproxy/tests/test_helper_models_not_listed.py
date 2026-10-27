"""Voice and helper model files are never listed as chat models (2026-10-07): LM Studio indexes the voice GGUFs that MultiProxy keeps in
`<models folder>/voice/...` and lists them as `models@f16`, `downloads@q8_0`. Neither LM Studio's list nor the folder scan may offer them."""
import struct

import pytest

import courier_models as cm
from tests.fixtures.courier_fakes import FakeLmStudio
from conftest import REAL_FETCH_LMSTUDIO_MODELS

JUNK = ["models@f16", "models@?", "downloads@q8_0", "downloads@f16", "downloads@?"]
LIB = [
    {"type": "llm", "modelKey": "google/gemma-4-12b-it-qat", "displayName": "Gemma 4 12B", "path": "google/gemma-4-12b-it-qat", "sizeBytes": 7_000_000_000, "arch": "gemma3"},
    {"type": "llm", "modelKey": "google/gemma-4-e4b-it", "displayName": "Gemma 4 E4B", "path": "google/gemma-4-e4b-it", "sizeBytes": 5_000_000_000},
] + [{"type": "llm", "modelKey": k, "displayName": "Qwen3 Asr", "path": "E:/LM Models/voice/models/x.gguf", "sizeBytes": 900_000_000} for k in JUNK[:2]] + [
    {"type": "llm", "modelKey": "tts-thing", "displayName": "Tts", "path": "x", "sizeBytes": 1, "arch": "qwen3tts"},
] + [{"type": "llm", "modelKey": k, "displayName": "Voice", "path": "E:/LM Models/voice/downloads/y.gguf", "sizeBytes": 900_000_000} for k in JUNK[2:]]


def test_the_rule():
    assert cm.is_helper_model("models@f16") and cm.is_helper_model("downloads@?")
    assert cm.is_helper_model(path="E:\\LM Models\\voice\\models\\a.gguf") and cm.is_helper_model(path="/m/decisions/a.gguf")
    assert cm.is_helper_model(arch="qwen3asr") and cm.is_helper_model(arch="Qwen3-TTS")
    assert not cm.is_helper_model("google/gemma-4-e4b-it", "google/gemma-4-e4b-it", "gemma3")
    assert not cm.is_helper_model("my-voice-model", "E:/m/my-voice-model.gguf")        # a name alone is never guessed from


@pytest.mark.asyncio
async def test_lm_studios_list_leaves_out_the_helper_files(proxy_module, monkeypatch):
    f = await FakeLmStudio(models=[dict(m) for m in LIB]).start()
    try:
        monkeypatch.setattr(proxy_module, "lmstudio_base_url", f.base + "/v1")
        proxy_module._lmstudio_cache.clear()
        got = await REAL_FETCH_LMSTUDIO_MODELS(f.base + "/v1", force=True)
        assert sorted(m["id"] for m in got) == ["google/gemma-4-12b-it-qat", "google/gemma-4-e4b-it"]
    finally:
        await f.stop()


@pytest.mark.asyncio
async def test_the_device_adapter_leaves_out_the_helper_files(proxy_module):
    import courier_adapters as ca
    f = await FakeLmStudio(models=[dict(m) for m in LIB]).start()
    try:
        a = ca.LmStudioAdapter(proxy_module.logger, base=f.base, cli="lms", runner=f.cli)
        ids = [m["id"] for m in await a.models()]
        assert sorted(ids) == ["google/gemma-4-12b-it-qat", "google/gemma-4-e4b-it"]
        assert not [i for i in ids if "@" in i]
    finally:
        await f.stop()


def _gguf(path, arch, size=60 * 1024 * 1024):
    path.parent.mkdir(parents=True, exist_ok=True)
    key = b"general.architecture"
    head = b"GGUF" + struct.pack("<IQQ", 3, 0, 1) + struct.pack("<Q", len(key)) + key + struct.pack("<I", 8) + struct.pack("<Q", len(arch)) + arch.encode()
    with open(path, "wb") as fh:
        fh.write(head)
        fh.truncate(size)


def test_the_folder_scan_lists_only_chat_models(tmp_path):
    _gguf(tmp_path / "google" / "gemma" / "gemma-4-e4b-Q4.gguf", "gemma3")
    _gguf(tmp_path / "qwen" / "qwen3-8b-Q4.gguf", "qwen3")
    _gguf(tmp_path / "voice" / "models" / "qwen3-asr-0.6b-f16.gguf", "qwen3asr")
    _gguf(tmp_path / "voice" / "downloads" / "chatterbox-q8_0.gguf", "chatterbox")
    _gguf(tmp_path / "decisions" / "Kev-4B-Q4_K_M.gguf", "qwen3")
    _gguf(tmp_path / "loose" / "some-speech-model.gguf", "whisper")       # outside the helper folders: the header says it is not text
    names = sorted(i["name"] for i in cm.find([("lmstudio", tmp_path)]))
    assert names == ["gemma-4-e4b-Q4", "qwen3-8b-Q4"]
    assert not [n for n in names if "@" in n]
    assert cm.describe(str(tmp_path / "voice" / "models" / "qwen3-asr-0.6b-f16.gguf")) is None
