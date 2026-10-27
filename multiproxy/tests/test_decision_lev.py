"""Lev, the third quick-decision model choice (2026-10-06): pinned like Kev-4B and Laya, Kev-4B still the default, and the same install and check path (stand-in file, no download)."""
import pytest

import decision_engine as de
from tests.decision_helpers import make_engine

REV = "3e9286a79ae857b4e1de051c92fab6dc574581ce"


class TestTable:
    def test_lev_is_pinned_by_revision_with_its_real_size_and_checksum(self):
        m = de.MODELS["lev"]
        assert m.label == "Lev" and m.model.name == "lev-Q4_K_M.gguf"
        assert m.model.url == f"https://huggingface.co/ggml-org/lev-GGUF/resolve/{REV}/lev-Q4_K_M.gguf"
        assert m.model.size == 3011777440 and m.model.sha256 == "3f61b27c00a098dbc79ed099cca3985cbe0b63d17ac7fedebf2dca1d84f7f3a8"

    def test_the_default_is_still_kev_4b_and_there_are_three_choices_in_order(self):
        assert de.DEFAULT_MODEL == "kev-4b"
        assert list(de.MODELS) == ["kev-4b", "laya", "lev"]

    def test_it_needs_no_special_flags_and_is_a_little_lighter_on_the_card_than_kev(self):
        m = de.MODELS["lev"]
        assert m.extra_args == () and m.encoder is False
        assert m.graphics_bytes < de.MODELS["kev-4b"].graphics_bytes and m.graphics_bytes > 3 * de.GIB


@pytest.mark.asyncio
class TestInstallAndStart:
    async def test_it_installs_checks_and_runs_like_the_others(self, tmp_path):
        engine, settings, _ = make_engine(tmp_path)
        settings["model"] = "lev"
        assert engine.spec().key == "lev"
        job = engine.start_install()
        await job.task
        assert job.state == "done"
        s = engine.status()
        assert s["state"] == "ready" and s["model"] == "lev"
        assert sorted(p.name for p in (tmp_path / "decisions" / "lev").iterdir()) == ["model-lev.gguf"]
        await engine.close()

    async def test_a_file_that_fails_its_check_is_refused(self, tmp_path):
        engine, settings, src = make_engine(tmp_path)
        settings["model"] = "lev"
        (src / "model-lev.gguf").write_bytes(b"not the pinned file")
        job = engine.start_install()
        await job.task
        assert job.state != "done" and engine.status()["state"] != "ready"
        await engine.close()
