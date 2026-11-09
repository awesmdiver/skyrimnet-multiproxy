"""The NVIDIA card read through its driver library (gpu_nvml.py): the same text and numbers as `nvidia-smi`, and no program started. Fake library only: this PC's real one is never opened."""
import logging

import pytest

import benchmarks_sampler as bsm
import gpu_nvml as gn
import hidden_process

MIB = 1024 * 1024
CARDS = [
    dict(name="NVIDIA GeForce RTX 5090", uuid="GPU-aaaa", total=32607, free=29000, used=3607, util=12, temp=48, power_mw=95430, limit_mw=575000, clock=2407, fan=31, margin=55),
    dict(name="NVIDIA GeForce RTX 3060", uuid="GPU-bbbb", total=12288, free=12000, used=288, util=0, temp=39, power_mw=None, limit_mw=170000, clock=210, fan=None, margin=62),     # no power or fan reading
]


class FakeLib:
    def __init__(self, cards=CARDS, margin=True, lose_after=None):
        self.cards, self.margin, self.lose_after, self.calls = cards, margin, lose_after, 0

    def _c(self, h): return self.cards[h]

    def _tick(self):
        self.calls += 1
        if self.lose_after is not None and self.calls > self.lose_after:
            raise gn.Lost("the card went away")

    def driver_version(self): return "572.16"
    def count(self): self._tick(); return len(self.cards)
    def handle(self, i): return i
    def name(self, h): return self._c(h)["name"]
    def uuid(self, h): return self._c(h)["uuid"]
    def memory(self, h): c = self._c(h); return (c["total"] * MIB, c["free"] * MIB, c["used"] * MIB)
    def utilization(self, h): return self._c(h)["util"]
    def temperature(self, h): return self._c(h)["temp"]
    def power_mw(self, h): return self._c(h)["power_mw"]
    def power_limit_mw(self, h): return self._c(h)["limit_mw"]
    def clock_sm(self, h): return self._c(h)["clock"]
    def fan(self, h): return self._c(h)["fan"]
    def margin_c(self, h): return self._c(h)["margin"] if self.margin else None


def smi_text(cards, fields):
    """What nvidia-smi prints for the same readings (the format it uses: whole numbers, power with two decimals, [N/A] for what the card does not give)."""
    def one(c, i, f):
        v = {"index": i, "name": c["name"], "uuid": c["uuid"], "memory.used": c["used"], "memory.total": c["total"], "memory.free": c["free"], "utilization.gpu": c["util"],
             "temperature.gpu": c["temp"], "temperature.gpu.tlimit": c["margin"], "clocks.sm": c["clock"], "fan.speed": c["fan"], "driver_version": "572.16",
             "power.draw": None if c["power_mw"] is None else "%.2f" % (c["power_mw"] / 1000), "power.limit": None if c["limit_mw"] is None else "%.2f" % (c["limit_mw"] / 1000)}[f]
        return "[N/A]" if v is None else str(v)
    return "".join(", ".join(one(c, i, f) for f in fields) + "\n" for i, c in enumerate(cards))


def install(monkeypatch, lib):
    monkeypatch.setattr(gn, "_opener", lambda: lib)
    gn.reset()
    return lib


# ---- the same numbers as nvidia-smi ----
def test_the_sampler_query_gives_the_same_rows_as_nvidia_smi_field_by_field(monkeypatch):
    install(monkeypatch, FakeLib())
    for cmd in (bsm.SMI_QUERY, bsm.SMI_QUERY_MARGIN):
        fields = gn.parse_command(cmd)
        got = gn.answer(cmd)
        assert got == smi_text(CARDS, fields)                                                  # the text itself is identical
        assert bsm.parse_gpu_rows(got) == bsm.parse_gpu_rows(smi_text(CARDS, fields))           # and so is every parsed reading
    rows = bsm.parse_gpu_rows(gn.answer(bsm.SMI_QUERY_MARGIN))
    assert rows[1]["power_w"] is None and rows[1]["fan_pct"] is None and rows[0]["t_limit_margin_c"] == 55          # a missing figure is None, never 0


def test_the_other_queries_match_too(monkeypatch):
    install(monkeypatch, FakeLib())
    for fields in (["memory.used", "memory.total"], ["uuid", "name", "memory.total"], ["name", "memory.total"], ["driver_version"], ["name", "memory.used", "memory.total", "utilization.gpu", "temperature.gpu"]):
        cmd = ["nvidia-smi", "--query-gpu=" + ",".join(fields), "--format=csv,noheader,nounits"]
        assert gn.answer(cmd) == smi_text(CARDS, fields), fields


def test_what_it_cannot_answer_goes_to_the_real_program(monkeypatch):
    install(monkeypatch, FakeLib())
    assert gn.answer(["nvidia-smi", "-q", "-d", "TEMPERATURE"]) is None                          # the heat thresholds text
    assert gn.answer(["nvidia-smi", "-i", "GPU-aaaa", "--query-gpu=memory.free", "--format=csv,noheader,nounits"]) is None
    assert gn.answer(["nvidia-smi", "--query-gpu=pcie.link.gen.max", "--format=csv,noheader,nounits"]) is None
    assert gn.answer(["tasklist"]) is None
    install(monkeypatch, FakeLib(margin=False))                                                  # a driver with no live margin: nvidia-smi decides (an old one refuses the field)
    assert gn.answer(bsm.SMI_QUERY_MARGIN) is None and gn.answer(bsm.SMI_QUERY) is not None


def test_throttle_reason_names():
    assert gn.throttle_names(0x4 | 0x40) == ["sw-power-cap", "hw-thermal"] and gn.throttle_names(0) == [] and gn.throttle_names(None) == []


# ---- no program per reading ----
def spawn_counter(monkeypatch):
    spawned = []

    def fake(cmd, **kw):
        spawned.append(list(cmd))
        raise FileNotFoundError("no nvidia-smi in this test")
    monkeypatch.setattr(hidden_process, "run", fake)
    return spawned


def test_a_fake_run_on_the_library_path_starts_no_program(monkeypatch):
    install(monkeypatch, FakeLib())
    spawned = spawn_counter(monkeypatch)
    rows = []
    s = bsm.ReadingsSampler(on_sample=rows.append, machine_reader=lambda: {"cpu_util_pct": 1.0, "ram_used_mb": 1000})        # the default runner
    for i in range(30):
        s.tick(float(i))
    assert spawned == [] and len([r for r in rows if r.get("gpu_index") == 0]) == 30 and rows[0]["vram_used_mb"] == 3607


def test_when_the_library_fails_mid_run_nvidia_smi_takes_over_and_one_line_is_logged(monkeypatch, caplog):
    install(monkeypatch, FakeLib(lose_after=3))
    spawned = []

    class P:
        returncode, stdout = 0, smi_text(CARDS, list(bsm._QUERY_FIELDS))

    monkeypatch.setattr(hidden_process, "run", lambda cmd, **kw: (spawned.append(list(cmd)), P)[1])
    caplog.set_level(logging.INFO, logger="multiproxy.gpu_nvml")
    s = bsm.ReadingsSampler(on_sample=lambda r: None, machine_reader=lambda: {"cpu_util_pct": 1.0})
    for i in range(10):
        s.tick(float(i))
    assert len(spawned) >= 6 and s.snapshot()["gpus"][0]["vram_used_mb"] == 3607                 # the real program carried on; the readings kept coming
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1 and "nvidia-smi is used from now on" in warnings[0].getMessage()
    assert not gn.available()                                                                   # off for the rest of the run
    gn.reset()                                                                                  # a new run (the sampler's start) tries again
    assert gn.available()


def test_no_library_at_all_is_the_old_path_unchanged(monkeypatch):
    def nolib():
        raise gn.Lost("no library")
    monkeypatch.setattr(gn, "_opener", nolib)
    gn.reset()
    assert gn.answer(bsm.SMI_QUERY) is None


def test_run_gives_a_finished_process_for_callers_that_want_one(monkeypatch):
    install(monkeypatch, FakeLib())
    spawn_counter(monkeypatch)
    r = gn.run(["nvidia-smi", "--query-gpu=uuid,name,memory.total", "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=10)
    assert r.returncode == 0 and r.stdout.splitlines()[0] == "GPU-aaaa, NVIDIA GeForce RTX 5090, 32607"
    with pytest.raises(FileNotFoundError):
        gn.run(["nvidia-smi", "-q"], capture_output=True)                                       # not answerable: the real program (the fake one here)
