"""Benchmarks: the model facts line, the cache cost at two sizes, the engine line and the VRAM spill line. Fake GGUF headers and fake readings only."""
import asyncio
import json
import struct
from types import SimpleNamespace

import pytest

import benchmarks_engine as be
import benchmarks_facts as bf
import benchmarks_runner as br
import gguf_header
import local_engine_memory as lem

MIB = 1024 * 1024


def write_gguf(path, kv):
    def s(x):
        b = x.encode()
        return struct.pack("<Q", len(b)) + b

    def val(v):
        if isinstance(v, str):
            return struct.pack("<I", 8) + s(v)
        if isinstance(v, list):
            return struct.pack("<I", 9) + struct.pack("<I", 4) + struct.pack("<Q", len(v)) + b"".join(struct.pack("<I", x) for x in v)
        if v > 0xFFFFFFFF:
            return struct.pack("<I", 10) + struct.pack("<Q", v)
        return struct.pack("<I", 4) + struct.pack("<I", v)
    body = b"".join(s(k) + val(v) for k, v in kv.items())
    path.write_bytes(b"GGUF" + struct.pack("<I", 3) + struct.pack("<Q", 0) + struct.pack("<Q", len(kv)) + body + b"\0" * 4096)
    return str(path)


PLAIN = {"general.architecture": "llama", "general.size_label": "12B", "general.file_type": 15, "llama.block_count": 40, "llama.context_length": 131072, "llama.embedding_length": 4096,
         "llama.attention.head_count": 32, "llama.attention.head_count_kv": 8}
SWA = dict(PLAIN, **{"general.architecture": "gemma3"})
SWA = {k.replace("llama.", "gemma3."): v for k, v in SWA.items()}
SWA.update({"gemma3.attention.sliding_window": 1024, "gemma3.attention.sliding_window_pattern": 6})
SETTINGS = {"cacheType": "f16", "layers": "all", "ctx": 8192, "parallel": 1}


# ---- the facts line ----
def test_facts_from_the_header(tmp_path):
    f = bf.model_facts(write_gguf(tmp_path / "a.gguf", PLAIN))
    assert f == {"arch": "llama", "params": "12B", "quant": "Q4_K_M", "layers": 40, "max_ctx": 131072}


def test_a_fact_the_file_does_not_give_is_left_out_never_zero(tmp_path):
    kv = {k: v for k, v in PLAIN.items() if k not in ("general.size_label", "general.file_type", "llama.context_length")}
    f = bf.model_facts(write_gguf(tmp_path / "b.gguf", kv))
    assert f == {"arch": "llama", "layers": 40}
    odd = bf.model_facts(write_gguf(tmp_path / "c.gguf", dict(PLAIN, **{"general.file_type": 9999})))
    assert "quant" not in odd                                                                 # an unknown file type is not guessed
    assert bf.model_facts(str(tmp_path / "missing.gguf")) == {}


def test_the_parameter_count_is_used_when_there_is_no_size_label(tmp_path):
    kv = {k: v for k, v in PLAIN.items() if k != "general.size_label"}
    kv["general.parameter_count"] = 12_300_000_000
    assert bf.model_facts(write_gguf(tmp_path / "d.gguf", kv))["params"] == "12.3B"


# ---- the cache cost ----
def test_the_cache_cost_is_our_own_estimate_at_two_sizes(tmp_path):
    path = write_gguf(tmp_path / "e.gguf", PLAIN)
    c = bf.cache_cost(path, SETTINGS, 4096)
    facts = gguf_header.read_model_facts(path)
    assert c["a_ctx"] == 4096 and c["b_ctx"] == 32768
    assert c["a_mb"] == lem.cache_bytes(facts, 4096, 1) // MIB and c["b_mb"] == lem.cache_bytes(facts, 32768, 1) // MIB          # exactly local_engine_memory's number
    assert c["b_mb"] == 8 * c["a_mb"] and c["a_mb"] == 640                                                                       # 40 layers x 8 heads x 128 x 2 values x 2 bytes x 4096


def test_the_second_size_is_the_models_own_longest_context_when_that_is_smaller(tmp_path):
    path = write_gguf(tmp_path / "f.gguf", dict(PLAIN, **{"llama.context_length": 16384}))
    c = bf.cache_cost(path, SETTINGS, 4096)
    assert c["b_ctx"] == 16384
    short = bf.cache_cost(write_gguf(tmp_path / "g.gguf", dict(PLAIN, **{"llama.context_length": 4096})), SETTINGS, 4096)
    assert "b_mb" not in short and short["a_mb"] == 640                                         # nothing bigger to show


def test_a_sliding_window_model_costs_less_than_the_same_model_with_full_attention(tmp_path):
    swa_path = write_gguf(tmp_path / "i.gguf", SWA)
    swa = bf.cache_cost(swa_path, SETTINGS, 4096)
    plain_kv = {k.replace("gemma3.", "llama."): v for k, v in SWA.items() if "sliding" not in k}
    plain_kv["general.architecture"] = "llama"
    plain = bf.cache_cost(write_gguf(tmp_path / "j.gguf", plain_kv), SETTINGS, 4096)
    assert swa["b_mb"] < plain["b_mb"] / 2                                                        # 5 of every 6 layers keep only their window at 32k
    facts = gguf_header.read_model_facts(swa_path)
    assert swa["b_mb"] == lem.cache_bytes(facts, 32768, 1) // MIB                                 # and it is local_engine_memory's number, not a flat formula


def test_no_cache_line_when_the_estimate_cannot_be_made(tmp_path):
    kv = {"general.architecture": "llama", "llama.block_count": 40}                                # no heads, no sizes
    assert bf.cache_cost(write_gguf(tmp_path / "k.gguf", kv), SETTINGS, 4096) == {}
    assert bf.cache_cost(str(tmp_path / "missing.gguf"), SETTINGS, 4096) == {}
    assert bf.for_model(str(tmp_path / "missing.gguf"), SETTINGS, 4096) == {}


def test_for_model_has_each_part_only_when_it_has_something(tmp_path):
    kv = {"general.architecture": "llama", "llama.block_count": 40}
    got = bf.for_model(write_gguf(tmp_path / "l.gguf", kv), SETTINGS, 4096)
    assert got == {"facts": {"arch": "llama", "layers": 40}}


# ---- the engine line ----
def test_the_engine_kind_comes_only_from_its_own_start_log():
    assert be.parse_backend("ggml_cuda_init: found 1 CUDA devices:\n  Device 0: NVIDIA GeForce RTX 5090") == "CUDA"
    assert be.parse_backend("ggml_vulkan: Found 1 Vulkan devices:") == "Vulkan"
    assert be.parse_backend("load_tensors:   CPU_Mapped model buffer size = 7000 MiB") is None            # silence is not 'CPU'
    assert be.parse_engine_log("ggml_vulkan: Found 1 Vulkan devices")["backend"] == "Vulkan"
    assert "backend" not in be.parse_engine_log("nothing useful")


def test_the_slots_answer():
    assert be.parse_slots([{"id": 0, "is_processing": True}, {"id": 1, "is_processing": False}, {"id": 2, "is_processing": False}]) == {"slots": 3, "busy": 1}
    for bad in (None, [], {"error": "off"}, [{"id": 0}], ["x"]):
        assert be.parse_slots(bad) is None


class Ctx(SimpleNamespace):
    pass


def run_note(launcher, resolved, sampler=None):
    status = {}
    ctx = Ctx(handle=SimpleNamespace(pid=77), svc=SimpleNamespace(_status=status, launcher=launcher), resolved=resolved, sampler=sampler)
    asyncio.run(br._RunCtx._note_engine(ctx))
    return status


def test_the_engine_line_has_the_name_build_and_kind_from_the_start_log_and_the_slots_from_its_route():
    class L:
        async def slot_state(self, h):
            return {"slots": 8, "busy": 3}
    st = run_note(L(), {"engine_build": "b11361", "backend": "CUDA"})
    assert st["engine"] == {"name": "llama.cpp", "build": "b11361", "backend": "CUDA", "slots": 8, "busy": 3}


def test_no_slots_when_the_status_route_is_silent_and_no_line_when_nothing_is_known():
    class Silent:
        async def slot_state(self, h):
            return None
    st = run_note(Silent(), {"engine_build": "b11361"})
    assert st["engine"] == {"name": "llama.cpp", "build": "b11361"}                       # no slots keys at all
    assert run_note(object(), {})["engine"] == {"name": "llama.cpp"}                       # a launcher with no status route: just what the log said
    assert "paged_mb" not in st


def test_the_paged_figure_is_kept_only_above_zero():
    for value, kept in ((0, False), (None, False), (512, True)):
        sampler = SimpleNamespace(peek_engine_shared_gpu_mb=lambda pid, v=value: v)
        st = run_note(object(), {}, sampler)
        assert ("paged_mb" in st) is kept
        if kept:
            assert st["paged_mb"] == 512


# ---- what the page draws ----
PRELUDE = r"""
const elements = {};
const document = { getElementById: id => elements[id] || null, createElement: () => ({ style: {}, querySelector: () => ({}) }), body: { appendChild() {} }, addEventListener() {}, removeEventListener() {}, activeElement: null };
const window = {};
function esc(s) { return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function jsq(s) { return esc(String(s == null ? '' : s).replace(/[\\']/g, '')); }
function ovDot(level) { return '<span class="ov-dot ' + esc(level) + '"></span>'; }
async function lmPost() { return {}; } function lmPause() {} function lmResume() {} function lmCancel() {} function lmDownloadModel() {}
async function ownAsk() { return true; }
async function fetch() { return { ok: true, status: 200, json: async () => ({}) }; }
function setTimeout(f, ms) { return 1; } function clearTimeout() {}
function copyModel() {} function mdLoad() {} function lmRefresh() {}
"""


def page(snippet, tmp_path):
    import shutil
    import subprocess
    import benchmarks_ui
    f = tmp_path / "t.js"
    f.write_text(PRELUDE + benchmarks_ui.BENCHMARKS_JS + "\n(async () => { const OUT = {};\n" + snippet + "\nconsole.log(JSON.stringify(OUT)); })().catch(e => { console.error(e.stack); process.exit(1); });\n", encoding="utf-8")
    node = shutil.which("node")
    if node is None:
        pytest.skip("Node isn't installed")
    r = subprocess.run([node, str(f)], capture_output=True, text=True, encoding="utf-8", timeout=60)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def text_of(html):
    import re
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", html.replace("&#39;", "'").replace("&amp;", "&"))).strip()


RUN = {"running": True, "model_index": 1, "models_total": 1, "model_name": "Gemma.gguf", "step": "level:4", "steps_done": 2, "steps_total": 7, "readings": None}
ROW = {"key": "c:a", "id": "a", "title": "Gemma 4 12B", "source": "community", "state": "downloaded", "ticked": False, "sizeBytes": 7000000000, "path": "C:/m/a.gguf"}


def test_the_setup_row_draws_the_facts_and_the_cache_lines(tmp_path):
    out = page("""
      const R = %s;
      OUT.full = bmModelRow(Object.assign({}, R, { facts: { arch: 'gemma3', params: '12B', quant: 'Q4_K_M', layers: 48, max_ctx: 131072 }, cache: { a_ctx: 4096, a_mb: 448, b_ctx: 32768, b_mb: 3584 } }));
      OUT.part = bmModelRow(Object.assign({}, R, { facts: { arch: 'gemma3' } }));
      OUT.none = bmModelRow(R);
    """ % json.dumps(ROW), tmp_path)
    t = text_of(out["full"])
    assert "gemma3 · 12B · Q4_K_M · 48 layers · up to 128k context" in t and "Cache: 4k context = 448 MB, 32k context = 3.5 GB" in t
    assert "gemma3" in out["part"] and "Cache:" not in out["part"] and "layers" not in out["part"]
    assert "bm-small" not in out["none"] and "Cache:" not in out["none"]


def test_the_live_header_order_and_its_two_short_columns(tmp_path):
    out = page("""
      const S = %s;
      OUT.all = bmRunHtml(Object.assign({}, S, { batch_started_epoch_s: 1, model_facts: { facts: { arch: 'gemma4', params: '4.6B', quant: 'Q4_K_M', layers: 35, max_ctx: 131072 }, cache: { a_ctx: 18432, a_mb: 120, b_ctx: 32768, b_mb: 204 } },
        engine: { name: 'llama.cpp', build: 'b11946', backend: 'CUDA', slots: 4, busy: 4 }, paged_mb: 130 }));
    """ % json.dumps(RUN), tmp_path)
    h = out["all"]
    order = [h.index(x) for x in ("Model 1 of 1: Gemma.gguf", "Stop benchmark", 'id="bmStepLine"', 'id="bmClock"', 'class="bm-bar"', "Model specs", "Live status", "bm-tiles")]
    assert order == sorted(order)                                                              # model + Stop, then step + timers, the bar right under them, the two columns, then the tiles
    t = text_of(h)
    assert "Architecture: gemma4 · 4.6B · Q4_K_M" in t and "Size: 35 layers · up to 128k context" in t and "Cache Est: 18k = 120 MB | 32k = 204 MB" in t
    assert "Engine: llama.cpp b11946 (CUDA)" in t and "Queue: 4 of 4 requests busy now" in t and "⚠️ VRAM spill: 130 MB" in t
    assert 'class="lm-warn bm-small"' in h and "VRAM spill: 130 MB" in h.split("lm-warn")[1]       # the page's warning style (amber text, no box)


def test_each_missing_fact_leaves_out_only_its_own_part(tmp_path):
    out = page("""
      const S = %s;
      OUT.noquant = bmRunHtml(Object.assign({}, S, { model_facts: { facts: { arch: 'llama', layers: 40 } } }));
      OUT.nolayers = bmRunHtml(Object.assign({}, S, { model_facts: { facts: { arch: 'llama', max_ctx: 8192 }, cache: { a_ctx: 4096, a_mb: 640 } } }));
      OUT.noengine = bmRunHtml(Object.assign({}, S, { engine: { name: 'llama.cpp', build: 'b1' } }));
      OUT.zero = bmRunHtml(Object.assign({}, S, { paged_mb: 0, engine: { name: 'llama.cpp' } }));
      OUT.bare = bmRunHtml(S);
    """ % json.dumps(RUN), tmp_path)
    t = text_of(out["noquant"])
    assert "Architecture: llama" in t and "Size: 40 layers" in t and "up to" not in t and "Cache Est" not in t and "Live status" not in t
    t = text_of(out["nolayers"])
    assert "Size: up to 8k context" in t and "layers" not in t and "Cache Est: 4k = 640 MB" in t and "|" not in t           # one size only: no separator
    t = text_of(out["noengine"])
    assert "Engine: llama.cpp b1" in t and "Queue" not in t and "VRAM spill" not in t
    assert "VRAM spill" not in out["zero"] and "Queue" not in out["zero"]
    for word in ("Model specs", "Live status", "Architecture", "Engine:", "Cache Est", "Queue"):
        assert word not in out["bare"]


def test_the_memory_bar_covers_the_whole_card(tmp_path):
    out = page("""
      const S = %s;
      const rd = (used, total) => ({ gpus: [{ gpu_index: 0, vram_used_mb: used, vram_total_mb: total }], peaks: {}, machine: null });
      const mm = { slots: 4, model: 8000, cache: 2000, compute: 1000 };
      OUT.whole = bmLiveMemHtml(Object.assign({}, S, { memory_model: mm, readings: rd(16000, 32000), model_name: 'M.gguf' }));
      OUT.nouse = bmLiveMemHtml(Object.assign({}, S, { memory_model: mm, readings: rd(null, 32000), model_name: 'M.gguf' }));
      OUT.nototal = bmLiveMemHtml(Object.assign({}, S, { memory_model: mm, readings: null, model_name: 'M.gguf' }));
      OUT.users = bmLiveMemHtml(Object.assign({}, S, { memory_model: mm, readings: rd(16000, 32000), model_name: 'M.gguf', card_users: [{ name: 'Skyrim', mb: 5000 }] }));
    """ % json.dumps(RUN), tmp_path)
    import re as _re

    def widths(h):
        return [float(x) for x in _re.findall(r'<i class="bm-k\d" style="width:([\d.]+)%', h)]
    assert widths(out["whole"]) == [25.0, 6.3, 3.1, 15.6]                                    # of 32 GB: the model, the cache, working space, everything else (16000 - 11000); the rest is free
    assert 'title="The model · 7.8 GB"' in out["whole"] and "Everything else · 4.9 GB" in out["whole"]
    assert widths(out["nouse"]) == [25.0, 6.3, 3.1]                                          # no measured card use: no 'everything else' segment, the rest still to the real total
    assert widths(out["nototal"]) == [72.7, 18.2, 9.1]                                       # no card total: as before, the three measured parts fill the bar
    assert out["users"].count("<tr>") == 4 and "Skyrim" in out["users"]                      # the two lists are still there
    assert 'class="bm-key bm-k1"' in out["whole"] and 'class="bm-key bm-k4"' in out["users"]  # the markers use the same colours as the segments
