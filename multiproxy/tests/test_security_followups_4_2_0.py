"""Security review 4.2.0 follow-ups: S1 (the clip facts line from a hostile header) and S4 (a cached runtime that is not the pinned one is never reused)."""
import struct
import sys
from pathlib import Path

import pytest

import voice_clips as vc

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "installer"))
import build as ib  # noqa: E402

FMT = b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, 24000, 48000, 2, 16)


def wav(parts: bytes) -> bytes:
    return b"RIFF" + struct.pack("<I", 0) + b"WAVE" + FMT + parts + b"data" + struct.pack("<I", 0)


def clean(line: str) -> bool:
    return line.isascii() and line.isprintable() and "\n" not in line and "\r" not in line and "\x1b" not in line


def test_a_hostile_part_name_is_printable_and_short():
    line = vc.clip_facts(wav(b"a\nb\x1b" + struct.pack("<I", 0) + b"\xff\x00\x07\x7f" + struct.pack("<I", 0)))
    assert clean(line) and "a?b?" in line and "?" in line


def test_a_clip_of_100000_tiny_parts_gives_a_short_one_line_fact():
    parts = b"".join(b"ab\n\x1b" + struct.pack("<I", 0) for _ in range(100_000))
    line = vc.clip_facts(wav(parts))
    assert clean(line) and len(line) <= vc.FACTS_MAX_CHARS
    assert line.count("ab??") <= vc.FACTS_MAX_PARTS


def test_the_walk_stops_early(monkeypatch):
    seen = []
    real = vc._next_part
    monkeypatch.setattr(vc, "_next_part", lambda d, b, s: seen.append(1) or real(d, b, s))
    vc.clip_facts(wav(b"".join(b"abcd" + struct.pack("<I", 0) for _ in range(100_000))))
    assert len(seen) <= vc.FACTS_MAX_WALK


def test_a_normal_clip_still_reads_the_same():
    assert vc.clip_facts(wav(b"LIST" + struct.pack("<I", 0))).startswith("PCM, 1 channel, 24000 Hz, 16-bit,")
    assert "extra parts before the sound: LIST" in vc.clip_facts(wav(b"LIST" + struct.pack("<I", 0)))


def make_runtime(path: Path, pin: str = None):
    path.mkdir(parents=True)
    (path / "python.exe").write_bytes(b"")
    (path / f"python{ib.PY_TAG}.zip").write_bytes(b"")
    if pin is not None:
        (path / ib.RUNTIME_PIN_FILE).write_text(pin, encoding="utf-8")


def test_a_runtime_built_from_the_pinned_python_is_reused(tmp_path):
    make_runtime(tmp_path / "r", ib.runtime_pin())
    assert ib.runtime_is_pinned(tmp_path / "r")


@pytest.mark.parametrize("pin", [None, "python 3.14.7\nsha256 " + "0" * 64 + "\n", ""])
def test_a_runtime_not_built_from_the_pinned_python_is_not_reused(tmp_path, pin):
    make_runtime(tmp_path / "r", pin)
    assert not ib.runtime_is_pinned(tmp_path / "r")


def test_runtime_for_rebuilds_a_stale_cache_instead_of_using_it(tmp_path, monkeypatch):
    monkeypatch.setattr(ib, "CACHE", tmp_path)
    built = []
    monkeypatch.setattr(ib, "build_runtime", lambda name, dest: built.append(dest))
    key_dir = None
    # first call finds nothing and builds
    dest = ib.runtime_for("multiproxy")
    assert built == [dest]
    # a leftover folder with the right name but no pin file is still rebuilt
    make_runtime(dest) if not dest.exists() else (dest.mkdir(parents=True, exist_ok=True), (dest / "python.exe").write_bytes(b""))
    built.clear()
    ib.runtime_for("multiproxy")
    assert built == [dest]
    # one that carries the pin is reused
    (dest / f"python{ib.PY_TAG}.zip").write_bytes(b"")
    (dest / ib.RUNTIME_PIN_FILE).write_text(ib.runtime_pin(), encoding="utf-8")
    built.clear()
    ib.runtime_for("multiproxy")
    assert built == []
