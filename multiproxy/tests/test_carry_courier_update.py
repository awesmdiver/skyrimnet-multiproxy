"""The build step that puts the signed Courier update next to MultiProxy (installer/build.py and scripts/build_linux_packages.py), checked with throwaway keys in scratch folders."""
import importlib.util
from pathlib import Path

import pytest

import courier_keys
from tests.fixtures import update_pkg as pk

ROOT = Path(__file__).resolve().parent.parent


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ib = load("installer/build.py", "carry_ib")
blp = load("scripts/build_linux_packages.py", "carry_blp")


@pytest.fixture(autouse=True)
def keys(monkeypatch):
    monkeypatch.setattr(courier_keys, "CURRENT", pk.PUBLIC_B64)
    monkeypatch.setattr(courier_keys, "NEXT", "")
    for m in (ib, blp._ib):
        monkeypatch.setattr(m, "current_courier_build", lambda: 7)       # the stand-in packages below are build 7


def test_a_package_of_another_build_is_refused_and_says_which(tmp_path, monkeypatch):
    src = tmp_path / "dist" / "MultiProxy-Courier-Update.zip"
    src.parent.mkdir()
    src.write_bytes(pk.package_bytes(9))
    stage = tmp_path / "stage"
    stage.mkdir()
    for m in (ib, blp._ib):
        monkeypatch.setattr(m, "current_courier_build", lambda: 11)
    with pytest.raises(ib.BuildError) as e:
        ib.carry_courier_update(stage, source=src)
    assert "build 9" in str(e.value) and "build 11" in str(e.value) and "dist/MultiProxy-Courier-Update.zip" in str(e.value) and not (stage / "courier-update").exists()
    monkeypatch.setattr(blp._ib, "COURIER_UPDATE_SOURCE", src)
    with pytest.raises(blp.BuildError) as e2:
        blp._courier_update_entry()
    assert "build 9" in str(e2.value)


def test_the_build_reads_the_courier_build_from_courier_py():
    import courier
    monkeypatch_free = load("installer/build.py", "carry_ib_fresh")
    assert monkeypatch_free.current_courier_build() == courier.COURIER_BUILD


def test_a_signed_update_is_copied_into_the_stage(tmp_path):
    src = tmp_path / "dist" / "MultiProxy-Courier-Update.zip"
    src.parent.mkdir()
    src.write_bytes(pk.package_bytes(7))
    stage = tmp_path / "stage"
    stage.mkdir()
    assert ib.carry_courier_update(stage, source=src) == ["courier-update/MultiProxy-Courier-Update.zip"]
    assert (stage / "courier-update" / "MultiProxy-Courier-Update.zip").read_bytes() == src.read_bytes()


def test_without_one_a_dev_build_goes_on_and_a_release_build_stops(tmp_path):
    stage = tmp_path / "stage"
    stage.mkdir()
    assert ib.carry_courier_update(stage, source=tmp_path / "nothing.zip") == [] and not (stage / "courier-update").exists()
    with pytest.raises(ib.BuildError) as e:
        ib.carry_courier_update(stage, source=tmp_path / "nothing.zip", required=True)
    assert "sign it" in str(e.value)


def test_a_badly_signed_file_is_never_shipped(tmp_path):
    src = tmp_path / "bad.zip"
    src.write_bytes(pk.package_bytes(7, seed=pk.OTHER_SEED))
    stage = tmp_path / "stage"
    stage.mkdir()
    with pytest.raises(ib.BuildError) as e:
        ib.carry_courier_update(stage, source=src)
    assert "bad_signature" in str(e.value) and not (stage / "courier-update").exists()


def test_the_linux_package_carries_it_the_same_way(tmp_path, monkeypatch):
    src = tmp_path / "ok.zip"
    src.write_bytes(pk.package_bytes(7))
    monkeypatch.setattr(blp._ib, "COURIER_UPDATE_SOURCE", src)
    assert blp._courier_update_entry() == [(src, "courier-update/MultiProxy-Courier-Update.zip")]
    assert any(d == "courier-update/MultiProxy-Courier-Update.zip" for _, d in blp.collect("multiproxy"))
    assert not any("courier-update" in d for _, d in blp.collect("courier")), "a Courier never carries an update for others"
    src.write_bytes(b"junk")
    with pytest.raises(blp.BuildError):
        blp._courier_update_entry()
    monkeypatch.setattr(blp._ib, "COURIER_UPDATE_SOURCE", tmp_path / "gone.zip")
    assert blp._courier_update_entry() == []
    monkeypatch.setattr(blp, "REQUIRE_COURIER_UPDATE", True)
    with pytest.raises(blp.BuildError):
        blp._courier_update_entry()


def test_the_stage_of_the_courier_itself_never_carries_one(tmp_path):
    assert "courier-update" not in "".join(ib.stage_program("courier", tmp_path / "c"))
