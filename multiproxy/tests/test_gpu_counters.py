"""The graphics-card counters (gpu_counters.py): read in English through one open query, `Local Usage` / `Non Local Usage` first, per adapter, absent never zero.
Everything here runs on fake counters; the real Windows counter library is never touched."""
import fake_gpu_counters as fgc
import pytest

import benchmarks_sampler as bsm
import gpu_counters as gc
import gpu_monitor as gm

MIB = 1024 * 1024
E = gc.COUNTERS


def card(monkeypatch, rows, **kw):
    return fgc.install(monkeypatch, fgc.FakeBackend(fgc.table(rows, **kw)))


# ---- English names on any Windows language ----
def test_every_counter_is_asked_for_by_its_english_name(monkeypatch):
    backend = card(monkeypatch, [(100, 6100, 10)])
    progs = gc.read_programs()
    assert progs[100]["dedicated_mb"] == 6100
    assert backend.asked and set(backend.asked) <= fgc.ENGLISH              # nothing was asked in a translated form
    # the stand-in refuses a translated name (what a Russian Windows would need from typeperf), so a path that is not English would have found nothing
    assert backend.add(r"\Память процесса GPU(*)\Локальное использование") is None


def test_the_old_typeperf_path_finds_nothing_on_a_translated_windows_and_the_new_one_does(monkeypatch):
    """A fake localized typeperf: it only knows the translated names, so the English command fails with its usual error and the old reader gets nothing."""
    def localized_typeperf(cmd):
        if any(r"GPU Process Memory" in part for part in cmd):
            return "Error: No valid counters.\n"                     # typeperf: the English name does not exist on this Windows
        return ""
    old = gm.parse_typeperf_gpu_process_memory(localized_typeperf(["typeperf", r"\GPU Process Memory(*)\Dedicated Usage", "-sc", "1"]))
    assert old == {}
    card(monkeypatch, [(100, 6100, 10)])
    assert gc.read_programs() == {100: {"dedicated_mb": 6100, "shared_mb": 10, "gpu_pct": None}}


# ---- which counters ----
def test_local_and_non_local_usage_win_over_dedicated_and_shared(monkeypatch):
    card(monkeypatch, [(300, 900, 0)], over_count={300: (15000, 4000)})        # the desktop: 15 GB "seen", 0.9 GB charged
    got = gc.read_programs()[300]
    assert got["dedicated_mb"] == 900 and got["shared_mb"] == 0
    assert gc.read_snapshot().which["dedicated"].endswith("Local Usage") and gc.read_snapshot().which["shared"].endswith("Non Local Usage")


def test_the_older_counters_are_the_fallback_on_a_windows_without_the_new_pair(monkeypatch):
    card(monkeypatch, [(300, 900, 5)], new_names=False)
    snap = gc.read_snapshot()
    assert snap.which["dedicated"].endswith("Dedicated Usage") and snap.which["shared"].endswith("Shared Usage")
    assert gc.read_programs()[300] == {"dedicated_mb": 900, "shared_mb": 5, "gpu_pct": None}


def test_a_counter_that_cannot_be_read_is_absent_never_zero(monkeypatch):
    t = fgc.table([(100, 6100, 10)])
    del t[E["shared"][0]], t[E["shared"][1]]                                   # no shared counter at all
    fgc.install(monkeypatch, fgc.FakeBackend(t))
    got = gc.read_programs()[100]
    assert got["dedicated_mb"] == 6100 and got["shared_mb"] is None and got["gpu_pct"] is None


def test_nothing_readable_gives_none_not_an_empty_card(monkeypatch):
    fgc.install(monkeypatch, fgc.FakeBackend({}))
    assert gc.read_programs() is None and gc.read_pid(1) is None


def test_the_busiest_engine_wins(monkeypatch):
    t = fgc.table([(100, 6100, 10)])
    p = fgc.inst(100)
    t[E["engine"][0]] = [(f"{p}_eng_0_engtype_3D", 12.0), (f"{p}_eng_1_engtype_Compute_0", 70.0), (f"{p}_eng_2_engtype_Copy", 3.0), (f"{p}_eng_3_engtype_VideoDecode", 1.0)]
    fgc.install(monkeypatch, fgc.FakeBackend(t))
    assert gc.read_programs()[100]["gpu_pct"] == 70.0
    assert gc.engine_kind("Compute_0") == "compute" and gc.engine_kind("VideoEncode") == "encode" and gc.engine_kind("Odd") == "other"


def test_the_query_is_opened_once_and_closed_on_exit(monkeypatch):
    backend = card(monkeypatch, [(100, 6100, 10)])
    for _ in range(5):
        gc.read_programs()
    assert backend.asked.count(E["dedicated"][0]) == 1                          # one open query, not one per reading
    gc.reset()
    assert backend.closed


# ---- the card the page uses ----
def test_several_cards_the_list_is_for_the_card_the_page_chose(monkeypatch):
    big, small = 0xF123, 0xA001
    t = fgc.table([(100, 6100, 0)], adapters={big: 20000, small: 800})
    t[E["dedicated"][0]] += [(fgc.inst(7, small), float(700 * MIB))]
    t[E["a_dedicated"][0]] = [(fgc.adapter_inst(big), float(20000 * MIB)), (fgc.adapter_inst(small), float(800 * MIB))]
    fgc.install(monkeypatch, fgc.FakeBackend(t))
    assert set(gc.read_programs(card_used_mb=20000)) == {100}                    # the big card
    assert set(gc.read_programs(card_used_mb=800)) == {7}                        # the built-in one
    assert set(gc.read_programs()) == {100}                                      # the card did not say: the one holding the most


def test_one_card_changes_nothing(monkeypatch):
    card(monkeypatch, [(1, 10, 0), (2, 20, 0)])
    assert set(gc.read_programs(card_used_mb=99999)) == {1, 2}


# ---- the readers ----
def test_the_sampler_reads_the_engine_shared_memory_from_non_local_usage(monkeypatch):
    card(monkeypatch, [(400, 14800, 12)], over_count={400: (14800, 5000)})       # the old Shared counter sees 5 GB of surfaces shared with other programs
    s = bsm.ReadingsSampler(machine_reader=lambda: {})
    assert s.read_engine_shared_gpu_mb(400) == 12
    assert s.read_engine_shared_gpu_mb(999) is None                              # not listed: absent, not zero


def _spill_after(monkeypatch, table_rows_over_time, over=None):
    s = bsm.ReadingsSampler(machine_reader=lambda: {})
    for rows in table_rows_over_time:
        fgc.install(monkeypatch, fgc.FakeBackend(fgc.table(rows, over_count=over(rows) if over else None)))
        s.read_engine_shared_gpu_mb(400)
    return s.spill()


def test_the_spill_evidence_still_fires_on_a_real_rise_of_the_charged_shared_memory(monkeypatch):
    got = _spill_after(monkeypatch, [[(400, 14000, 0)], [(400, 14000, 300)]])
    assert got["spill"] == "detected" and "rose by 300 MB" in got["evidence"]


def test_the_spill_evidence_does_not_fire_when_only_the_over_counting_counters_rise(monkeypatch):
    # Dedicated / Shared count surfaces shared from other programs: they jump by 2 GB while nothing is charged to the engine
    got = _spill_after(monkeypatch, [[(400, 14000, 0)], [(400, 14000, 0)]], over=lambda rows: {400: (16000, 2048)})
    assert got["spill"] != "detected"


def test_gpu_monitor_names_programs_without_tasklist_and_charges_local_usage(monkeypatch):
    card(monkeypatch, [(4242, 20480, 0), (7777, 9318, 0)], over_count={4242: (30000, 0), 7777: (9318, 0)})
    monkeypatch.setattr(gc, "process_names", lambda: {4242: "SkyrimSE.exe", 7777: "llama-server.exe", 555: "chrome.exe"})
    monkeypatch.setattr(gm, "_run", lambda cmd, timeout=15: "21000, 32607\n" if cmd[0] == "nvidia-smi" else (_ for _ in ()).throw(AssertionError(cmd)))   # tasklist / typeperf are never started
    mon = gm.GpuMonitor.__new__(gm.GpuMonitor)
    s = mon._read_windows(None, {"gemma": 7777}, True)
    assert s.skyrim_running and s.skyrim_mb == 20480 and s.models == {"gemma": 9318} and s.source == "windows-counters"
    assert mon.skyrim_alive() is True


def test_nothing_is_read_off_windows(monkeypatch):
    monkeypatch.setattr(gc, "_windows", lambda: False)
    assert gc.read_programs() is None and gc.process_names() == {}
