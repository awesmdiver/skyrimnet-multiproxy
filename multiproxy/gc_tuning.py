"""Garbage-collection settings for the server (2026-10-02).

Measured (perf check): in a burst of 100 requests at once the event loop was held 65-103 ms once per burst by a garbage-collection pass
(a collection of the oldest objects walks every object the program has made since start-up: the modules, the big page strings, the
routing tables). Two cheap, standard remedies: `gc.freeze()` once start-up is over moves everything alive at that moment out of the
collector's sight for good (those objects live as long as the program does), and a larger first-generation threshold makes the small, quick
collections happen less often during a burst. Nothing is switched off: cycles are still collected, only the long-lived start-up objects are
no longer re-examined every time.

`MULTIPROXY_GC_TUNE=0` in the environment turns this off (used to measure before and after).
"""
import gc
import os

GEN0_THRESHOLD = 20000       # (Python's default is 700 in older versions, 2000 in newer ones)


def apply() -> bool:
    """Freeze what exists now and raise the first-generation threshold. Call once, after every import, before the server starts."""
    if os.environ.get("MULTIPROXY_GC_TUNE", "").strip() == "0":
        return False
    gc.collect()
    gc.freeze()
    g0, g1, g2 = gc.get_threshold()[:3]
    gc.set_threshold(max(g0, GEN0_THRESHOLD), g1, g2)
    return True
