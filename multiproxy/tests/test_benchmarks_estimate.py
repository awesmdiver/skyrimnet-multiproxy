"""Benchmarks protect the PC, step 2: the memory a test really needs at the chosen number of requests at once (the KV formula against hand-computed cases, the model-file header reader on
hostile files, measured-run reuse, the margin and the three levels). No real model, no real engine."""
import json
import struct
import time

import pytest

import benchmarks_estimate as be
import gguf_header
import local_engine_memory as lem
import local_models
import set_me_up

MIB = 1024 * 1024


def plain(layers=32, heads=32, kv_heads=8, emb=4096, size=4_000_000_000):
    """A plain model (no sliding window): the key and value length are the embedding size over the head count (128 here)."""
    kv = {"llama.block_count": layers, "llama.attention.head_count": heads, "llama.attention.head_count_kv": kv_heads, "llama.embedding_length": emb, "llama.context_length": 8192}
    return gguf_header.ModelFacts("llama", layers, 8192, size, kv)


def need(facts, cache="f16", slots=2, per_slot=4096, **kw):
    return be.estimate_gpu_need(4_000_000_000, facts, {"cacheType": cache, "layers": "all", **kw.pop("settings", {})}, slots, per_slot, **kw)


# ---- the KV cache, by hand ----
# 2 slots of 4,096 tokens = 8,192 cells. A layer holds cells x KV heads x (key length x bytes per key + value length x bytes per value); 32 layers; key and value length 128.
#   f16:  8192 x 8 x (128x2 + 128x2)       = 33,554,432 per layer -> x32 = 1,073,741,824 bytes = 1,024 MiB
#   q8_0: 8192 x 8 x (128x34/32 x 2)       = 17,825,792 per layer -> x32 =   570,425,344 bytes =   544 MiB   (34 bytes per block of 32: the scale included)
#   q4_0: 8192 x 8 x (128x18/32 x 2)       =  9,437,184 per layer -> x32 =   301,989,888 bytes =   288 MiB
@pytest.mark.parametrize("cache,mib", [("f16", 1024), ("q8_0", 544), ("q4_0", 288)])
def test_the_cache_follows_the_header_by_hand_for_each_cache_type(cache, mib):
    n = need(plain(), cache)
    assert n.parts["cacheMb"] == mib and n.source == be.SOURCE_HEADER and n.confidence == be.CONFIDENCE_GOOD and n.ctx == 8192


def test_fewer_kv_heads_mean_proportionally_less_cache_and_more_slots_more_cache():
    assert need(plain(kv_heads=4)).parts["cacheMb"] == 512 and need(plain(kv_heads=2)).parts["cacheMb"] == 256
    assert need(plain(), slots=8, per_slot=4096).parts["cacheMb"] == 4096                 # 4x the slots at the same room each: 4x the memory
    assert need(plain(), slots=1, per_slot=8192).parts["cacheMb"] == 1024                 # one slot of 8,192 is the same cells as 2 of 4,096


def test_the_total_is_weights_cache_working_memory_and_overhead():
    n = need(plain())
    p = n.parts
    assert n.need_mb == p["weightsMb"] + p["visionMb"] + p["cacheMb"] + p["computeMb"] + p["engineMb"]
    assert p["weightsMb"] == int(4_000_000_000 * lem.WEIGHTS_FACTOR / MIB) and p["engineMb"] == lem.PROCESS_OVERHEAD_MB
    assert need(plain(), settings={"layers": "none"}).parts["weightsMb"] == 0               # nothing on the card


def test_the_context_is_at_least_the_room_each_slot_needs_the_way_the_launcher_sets_it():
    settings, over = be.run_settings({"ctx": 32768, "cacheType": "q8_0"}, {"cacheType": "f16", "layers": "all"}, 8, 18432)
    assert settings["cacheType"] == "f16" and over == {"ctx": 147456, "parallel": 8}      # 8 x 18,432 beats the device's 32,768
    assert be.run_settings({"ctx": 200000}, {}, 8, 18432)[1]["ctx"] == 200000
    assert be.run_settings({}, {"ctx": 4096}, 1, 18432)[1]["ctx"] == 18432


# ---- the real case: Gemma 31B on a 31.8 GB card (2026-10-28) ----
def facts31():
    return set_me_up.facts_of("gemma-4-31b", 18_687_063_904)


def test_the_31b_estimate_matches_what_the_one_slot_run_really_took():
    """Measured that evening: after the load, at 1 slot and 32,768 tokens, the card held 27,295 MB against 4,937 MB before: 22,358 MB. The header numbers (read from the real file's
    header, read-only) give 22,214 MB: under 1% off."""
    n = be.estimate_gpu_need(18_687_063_904, facts31(), {"cacheType": "f16", "layers": "all", "ctx": 32768}, 1, 18432)             # (the device was set to 32,768 tokens)
    assert n.ctx == 32768 and abs(n.need_mb - 22358) / 22358 < 0.01 and n.confidence == "good"


def test_at_eight_slots_the_31b_needs_more_than_the_card_has_which_is_why_the_pc_crawled():
    n = be.estimate_gpu_need(18_687_063_904, facts31(), {"cacheType": "f16", "layers": "all"}, 8, 18432)
    assert 38000 < n.need_mb < 41000 and n.parts["cacheMb"] == 21120                          # 39.6 GB wanted
    card = 32607
    assert be.verdict(n, card - 4937) == be.TOO_BIG and be.verdict(n, card) == be.TOO_BIG     # the card was full (32,108 of 32,607 MB used) and the rest spilled into main memory
    assert n.need_mb - (32108 - 4937) > 10000                                                  # about 12 GB more than the card could give it


# ---- the margin and the three levels ----
def test_the_margin_is_the_larger_of_one_gib_and_fifteen_percent():
    assert be.margin_mb(100) == 1024 and be.margin_mb(6800) == 1024 and be.margin_mb(6900) == 1035 and be.margin_mb(20000) == 3000


def make(need_mb, confidence=be.CONFIDENCE_GOOD):
    return be.Need(need_mb, be.margin_mb(need_mb), confidence, be.SOURCE_HEADER, 8, 18432, 147456)


def test_too_big_over_the_free_memory_tight_inside_the_margin_and_nothing_when_it_fits():
    n = make(10000)                                                      # margin 1,500
    assert be.verdict(n, 9999) == be.TOO_BIG
    assert be.verdict(n, 10000) == be.TIGHT and be.verdict(n, 11499) == be.TIGHT
    assert be.verdict(n, 11500) == be.FITS and be.verdict(n, 40000) == be.FITS
    assert be.verdict(n, None) is None                                    # an unknown card is never guessed at


def test_a_rough_estimate_is_never_better_than_amber():
    w = make(1000, be.CONFIDENCE_WEAK)
    assert be.verdict(w, 100000) == be.TIGHT and be.verdict(w, 500) == be.TOO_BIG


# ---- unknown models are weak ----
def test_a_model_the_header_cannot_describe_is_marked_weak_and_still_gets_a_number():
    for facts in (None, gguf_header.ModelFacts("mamba9", 24, 4096, 3_000_000_000, {}), plain(heads=None, kv_heads=None, emb=None)):
        n = be.estimate_gpu_need(3_000_000_000, facts, {"cacheType": "f16", "layers": "all"}, 8, 18432)
        assert n.source == be.SOURCE_FALLBACK and n.confidence == be.CONFIDENCE_WEAK and n.need_mb > 3000 and n.reason
    n = be.estimate_gpu_need(3_000_000_000, None, {"layers": "all"}, 8, 18432)
    assert n.parts["cacheMb"] == int(0.025 * 3.0 * 147456)               # the per-token constant scales with the file


# ---- measured runs: only the same configuration ----
def entry(**kw):
    e = {"file": "m.gguf", "size": 4_000_000_000, "build": "b11946", "slots": 8, "ctx": 147456, "ctk": "f16", "ctv": "f16", "layers": "all", "flash": "auto", "cache": "f16", "batch": None,
         "added_mb": 12000.0, "capped": False, "level": 8}
    e.update(kw)
    return e


SET = {"layers": "all", "flashAttn": "auto", "cacheType": "f16"}


def test_a_measured_run_is_reused_only_for_the_very_same_configuration():
    runs = [entry()]
    assert be.measured_for(runs, "m.gguf", 4_000_000_000, 8, 147456, SET, "b11946") == 12000.0
    for changed in (dict(slots=4), dict(ctx=73728), dict(size=4_000_000_001), dict(file="other.gguf"), dict(build="b1"), dict(cache="q8_0"), dict(layers="auto"), dict(flash="on"), dict(batch=512)):
        assert be.measured_for([entry(**changed)], "m.gguf", 4_000_000_000, 8, 147456, SET, "b11946") is None, changed
    assert be.measured_for([entry(capped=True)], "m.gguf", 4_000_000_000, 8, 147456, SET, "b11946") is None       # a run that filled the card says nothing about what the model needs
    assert be.measured_for([entry(slots=1, added_mb=5000.0)], "m.gguf", 4_000_000_000, 8, 147456, SET, "b11946") is None     # a one-slot peak is never an eight-slot estimate
    n = be.estimate_gpu_need(4_000_000_000, plain(), SET, 8, 18432, measured_mb=12000.0)
    assert n.source == be.SOURCE_MEASURED and n.need_mb == 12000 and n.confidence == be.CONFIDENCE_GOOD


def test_the_runs_folder_gives_each_finished_level_with_the_card_use_it_added(tmp_path):
    def run(name, status, hi, total=32000, before=4000):
        d = tmp_path / name
        d.mkdir()
        (d / "manifest.json").write_text(json.dumps({"status": status, "model_file": "m.gguf", "model_size_bytes": 4_000_000_000, "requested_settings": {"overrides": {}},
                                                     "resolved_settings": {"engine_build": "b1", "layers_requested": "all", "flash_attn_requested": "auto", "cache_type_requested": "f16"}}), encoding="utf-8")
        (d / "summary.json").write_text(json.dumps({"memory": {"0": {"vram_idle_before_load_mb": before, "vram_total_mb": total}}, "levels": {
            "8": {"engine": {"slots": 8, "ctx": 147456}, "vram_highest_in_level_mb": {"0": hi}}}}), encoding="utf-8")
    run("a", "completed", 16000)
    run("b", "cancelled", 16000)
    run("c", "completed", 31900)                                          # the card was full: capped
    got = be.load_measured(tmp_path)
    assert [(e["added_mb"], e["capped"]) for e in got] == [(12000.0, False), (27900.0, True)]
    assert be.load_measured(tmp_path / "nope") == []


# ---- the model-file header reader on hostile files ----
def gguf(path, keys=(), count=None, magic=b"GGUF", version=3, tail=b""):
    out = magic + struct.pack("<I", version) + struct.pack("<Q", 0) + struct.pack("<Q", len(keys) if count is None else count)
    for k, t, payload in keys:
        out += struct.pack("<Q", len(k)) + k.encode() + struct.pack("<I", t) + payload
    path.write_bytes(out + tail)
    return str(path)


def s(text):
    return struct.pack("<Q", len(text)) + text.encode()


GOOD = [("general.architecture", 8, s("llama")), ("llama.block_count", 4, struct.pack("<I", 32)), ("llama.context_length", 4, struct.pack("<I", 8192)),
        ("llama.attention.head_count", 4, struct.pack("<I", 32)), ("llama.attention.head_count_kv", 4, struct.pack("<I", 8)), ("llama.embedding_length", 4, struct.pack("<I", 4096))]


def test_the_header_reader_reads_a_small_generated_file(tmp_path):
    f = gguf_header.read_model_facts(gguf(tmp_path / "ok.gguf", GOOD))
    assert f.arch == "llama" and f.layers == 32 and f.trained_ctx == 8192 and f.g("attention.head_count_kv") == 8
    n = be.estimate_gpu_need(4_000_000_000, f, {"cacheType": "f16", "layers": "all"}, 2, 4096)
    assert n.parts["cacheMb"] == 1024                                      # the whole chain, from bytes on disk to the number


@pytest.mark.parametrize("name,build", [
    ("truncated", lambda p: p.write_bytes(open(gguf(p, GOOD), "rb").read()[:70])),
    ("empty", lambda p: p.write_bytes(b"")),
    ("wrong magic", lambda p: gguf(p, GOOD, magic=b"GGUX")),
    ("wrong version", lambda p: gguf(p, GOOD, version=99)),
    ("absurd key count", lambda p: gguf(p, GOOD, count=10 ** 12)),
    ("more keys than the file has", lambda p: gguf(p, GOOD, count=5000)),
    ("absurd string length", lambda p: gguf(p, [("general.architecture", 8, struct.pack("<Q", 2 ** 62) + b"x")])),
    ("string longer than the file", lambda p: gguf(p, [("general.architecture", 8, struct.pack("<Q", 60 * 1024 * 1024) + b"x")])),
    ("absurd key length", lambda p: p.write_bytes(b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", 0, 1) + struct.pack("<Q", 2 ** 60) + b"k")),
    ("array of strings with an absurd count", lambda p: gguf(p, [("general.name", 9, struct.pack("<IQ", 8, 2 ** 60) + s("a"))])),
    ("array of numbers with an absurd count", lambda p: gguf(p, [("llama.x", 9, struct.pack("<IQ", 4, 2 ** 60))])),
    ("nested array", lambda p: gguf(p, [("general.x", 9, struct.pack("<IQ", 9, 1))])),
    ("unknown value type", lambda p: gguf(p, [("general.x", 77, b"")])),
    ("random bytes", lambda p: p.write_bytes(bytes(range(256)) * 40)),
])
def test_a_hostile_header_is_refused_quickly_without_a_crash_or_a_huge_allocation(tmp_path, name, build):
    p = tmp_path / "bad.gguf"
    build(p)
    t = time.monotonic()
    assert gguf_header.read_model_facts(str(p)) is None, name
    assert time.monotonic() - t < 2.0 and p.stat().st_size < 20000


def test_an_unreadable_header_gives_a_weak_estimate_not_an_error(tmp_path):
    p = tmp_path / "bad.gguf"
    p.write_bytes(b"GGUX" + b"\0" * 500)
    n = be.estimate_gpu_need(p.stat().st_size, gguf_header.read_model_facts(str(p)), {"layers": "all"}, 4, 18432)
    assert n.confidence == be.CONFIDENCE_WEAK and n.source == be.SOURCE_FALLBACK
