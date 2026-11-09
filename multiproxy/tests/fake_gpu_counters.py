"""A stand-in for the Windows counter library: the data the real counters would give, and the rule that only the ENGLISH counter names are accepted (what `PdhAddEnglishCounterW`
does on any Windows language). Never touches this PC's counters."""
import gpu_counters as gc

MIB = 1024 * 1024
ENGLISH = {path for paths in gc.COUNTERS.values() for path in paths}


def inst(pid, luid=0xF123, phys=0):
    return f"pid_{pid}_luid_0x{luid >> 32:08X}_0x{luid & 0xFFFFFFFF:08X}_phys_{phys}"


def adapter_inst(luid=0xF123, phys=0):
    return f"luid_0x{luid >> 32:08X}_0x{luid & 0xFFFFFFFF:08X}_phys_{phys}"


class FakeBackend:
    """`table`: English counter path -> [(instance name, value)]. A path that is not in `table` cannot be added (a Windows build without it). `asked` lists every path add() saw."""

    def __init__(self, table, localized=False):
        self.table, self.localized, self.asked, self.collects, self.closed = table, localized, [], 0, False

    def add(self, path):
        self.asked.append(path)
        if path not in ENGLISH:                       # PdhAddEnglishCounterW knows only the English names; a translated one is refused
            return None
        return path if path in self.table else None

    def collect(self):
        self.collects += 1
        return True

    def read(self, handle):
        return list(self.table.get(handle, []))

    def close(self):
        self.closed = True


def table(rows, adapters=None, over_count=None, new_names=True, luid=0xF123):
    """`rows` = [(pid, local MB, non-local MB)] on one card. `over_count` = {pid: (dedicated MB, shared MB)}: the older counters, which see surfaces shared with other programs.
    `adapters` = {luid: dedicated MB}. `new_names=False`: a Windows build without Local / Non Local Usage."""
    t = {}
    local, nonlocal_, ded, sh = gc.COUNTERS["dedicated"][0], gc.COUNTERS["shared"][0], gc.COUNTERS["dedicated"][1], gc.COUNTERS["shared"][1]
    if new_names:
        t[local] = [(inst(p, luid), float(a * MIB)) for p, a, b in rows]
        t[nonlocal_] = [(inst(p, luid), float(b * MIB)) for p, a, b in rows]
    old = over_count if over_count is not None else {p: (a, b) for p, a, b in rows}
    t[ded] = [(inst(p, luid), float(a * MIB)) for p, (a, b) in old.items()]
    t[sh] = [(inst(p, luid), float(b * MIB)) for p, (a, b) in old.items()]
    if adapters:
        t[gc.COUNTERS["a_dedicated"][0]] = [(adapter_inst(l), float(mb * MIB)) for l, mb in adapters.items()]
    return t


def install(monkeypatch, backend):
    """Make the shared query use `backend`."""
    gc.reset()
    monkeypatch.setattr(gc, "_factory", lambda: gc.GpuCounters(backend))
    monkeypatch.setattr(gc, "_windows", lambda: True)
    return backend
