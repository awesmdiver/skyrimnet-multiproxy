"""The weights on the card from a model file's tensor table (gguf_tensors): partial offload by hand, a table that can't be read falls back, and the cache numbers
do not move. Small fake GGUF files only (no real model, no engine)."""
import struct

import pytest

import benchmarks_estimate as be
import gguf_header
import gguf_tensors as gt
import local_engine_memory as lem

MIB = 1024 * 1024


def _s(text):
    b = text.encode()
    return struct.pack("<Q", len(b)) + b


def write_gguf(path, tensors, layers, arch="llama", extra_kv=None, align=32, tie=False):
    """tensors: [(name, nbytes)] in file order. Offsets are laid out back to back, rounded to `align`, the way the converter writes them."""
    kv = [("general.architecture", 8, _s(arch)), (f"{arch}.block_count", 4, struct.pack("<I", layers)), (f"{arch}.context_length", 4, struct.pack("<I", 4096)),
          (f"{arch}.attention.head_count", 4, struct.pack("<I", 8)), (f"{arch}.attention.head_count_kv", 4, struct.pack("<I", 4)),
          (f"{arch}.embedding_length", 4, struct.pack("<I", 512))] + (extra_kv or [])
    head = b"GGUF" + struct.pack("<I", 3) + struct.pack("<QQ", len(tensors), len(kv))
    for k, t, v in kv:
        head += _s(k) + struct.pack("<I", t) + v
    offset, table, blobs = 0, b"", b""
    for name, n in tensors:
        table += _s(name) + struct.pack("<I", 1) + struct.pack("<Q", n) + struct.pack("<I", 0) + struct.pack("<Q", offset)
        pad = (-n) % align
        blobs += b"\0" * (n + pad)
        offset += n + pad
    body = head + table
    body += b"\0" * ((-len(body)) % align)
    path.write_bytes(body + blobs)
    return str(path)


def model(tmp_path, layers=4, tied=False, name="m.gguf"):
    t = [("token_embd.weight", 4000)]
    for i in range(layers):
        t.append((f"blk.{i}.attn_q.weight", 1024 + i * 32))
        t.append((f"blk.{i}.ffn_up.weight", 2016))
    t.append(("output_norm.weight", 64))
    if not tied:
        t.append(("output.weight", 4000))
    return write_gguf(tmp_path / name, t, layers)


def block(i):                      # what a block adds up to in model()
    return 1024 + i * 32 + 2016


def test_full_offload_is_every_block_the_output_and_never_the_input_embeddings(tmp_path):
    ts = gt.read_tensor_sizes(model(tmp_path))
    assert ts.layers == 4 and ts.token_embd_bytes == 4000 and ts.output_bytes == 4000 + 64
    assert ts.on_card_bytes(None) == sum(block(i) for i in range(4)) + 4064
    assert ts.on_card_bytes(-1) == ts.on_card_bytes(None) == ts.on_card_bytes(5)


def test_partial_offload_takes_the_output_first_then_the_last_blocks(tmp_path):
    ts = gt.read_tensor_sizes(model(tmp_path))
    # -ngl 3 = the output layer and the last 2 of 4 blocks (measured on the real engine: "offloading output layer to GPU", "offloading N-1 repeating layers")
    assert ts.on_card_bytes(3) == block(2) + block(3) + 4064
    assert ts.on_card_bytes(1) == 4064
    assert ts.on_card_bytes(0) == 0


def test_a_tied_output_puts_a_copy_of_the_input_embeddings_on_the_card(tmp_path):
    ts = gt.read_tensor_sizes(model(tmp_path, tied=True))
    assert ts.output_bytes == 4000 + 64
    assert ts.on_card_bytes(1) == 4064


def test_a_file_without_blocks_goes_up_whole(tmp_path):
    p = write_gguf(tmp_path / "clip.gguf", [("v.patch_embd.weight", 3008), ("v.blk_x.weight", 512)], 0, arch="clip")
    assert gt.read_tensor_sizes(p).on_card_bytes(7) == 3520


@pytest.mark.parametrize("name", ["missing", "magic", "short", "huge_count", "huge_dims"])
def test_a_table_that_cannot_be_read_gives_none(tmp_path, name):
    good = open(model(tmp_path), "rb").read()
    p = tmp_path / "bad.gguf"
    if name == "missing":
        p = tmp_path / "nope.gguf"
    elif name == "magic":
        p.write_bytes(b"GGUX" + good[4:])
    elif name == "short":
        p.write_bytes(good[:150])
    elif name == "huge_count":
        p.write_bytes(good[:8] + struct.pack("<Q", 2**40) + good[16:])
    elif name == "huge_dims":
        i = good.index(b"token_embd.weight") + len(b"token_embd.weight")
        p.write_bytes(good[:i] + struct.pack("<I", 999) + good[i + 4:])
    assert gt.read_tensor_sizes(str(p)) is None


def settings(layers):
    return {"layers": layers, "cacheType": "f16"}


def test_the_estimate_uses_the_table_for_the_weights_and_the_file_size_without_it(tmp_path):
    p = model(tmp_path, layers=4)
    size = len(open(p, "rb").read())
    facts, ts = gguf_header.read_model_facts(p), gt.read_tensor_sizes(p)
    with_table = lem.estimate_from_facts(size, None, facts, settings(3), {"ctx": 1024, "parallel": 1}, None, ts)
    assert with_table.weights_mb == int(ts.on_card_bytes(3) / MIB)
    without = lem.estimate_from_facts(size, None, facts, settings(3), {"ctx": 1024, "parallel": 1}, None, None)
    assert without.weights_mb == int(size * lem.WEIGHTS_FACTOR / MIB * 3 / facts.offload_layers)       # the old way, unchanged
    none = lem.estimate_from_facts(size, None, facts, settings("none"), {"ctx": 1024, "parallel": 1}, None, ts)
    assert none.weights_mb == 0


def test_the_cache_numbers_do_not_move_with_the_table(tmp_path):
    p = model(tmp_path, layers=4)
    size = len(open(p, "rb").read())
    facts, ts = gguf_header.read_model_facts(p), gt.read_tensor_sizes(p)
    for layers in ("all", 2):
        a = lem.estimate_from_facts(size, None, facts, settings(layers), {"ctx": 8192, "parallel": 2}, None, ts)
        b = lem.estimate_from_facts(size, None, facts, settings(layers), {"ctx": 8192, "parallel": 2}, None, None)
        assert a.cache_mb == b.cache_mb and a.compute_mb == b.compute_mb and a.exact == b.exact


def test_the_benchmarks_need_passes_the_table_through(tmp_path):
    p = model(tmp_path, layers=4)
    size = len(open(p, "rb").read())
    facts, ts = gguf_header.read_model_facts(p), gt.read_tensor_sizes(p)
    n = be.estimate_gpu_need(size, facts, {"layers": 3, "cacheType": "f16"}, 1, 1024, {}, None, None, ts)
    assert n.parts["weightsMb"] == int(ts.on_card_bytes(3) / MIB)
