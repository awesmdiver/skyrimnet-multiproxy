"""Skyrim's busiest graphics-memory moment: ONE stored figure that follows the player's real play (2026-10-26).

It used to be a single number that only ever went up, so one old spike made every model look too big for ever. Now the config keeps a small list of daily peaks and the figure
is the highest of the last 14 days: an old spike falls away, and a lower recent week brings the figure down. Everything that needs the figure reads it here: the Local device
dots, the memory guard's notes, the Models tab and Set me up (whose measurement is recorded into the same list).

Stored in config.json under `gpu_monitor`: `{"days": {"2026-10-26": 14336, ...}, "skyrimPeakMb": <window max>, "skyrimPeakDate": <its day>}`. The last two are kept only so
an older reader still works; `days` is the truth. An older config with just `skyrimPeakMb` / `skyrimPeakDate` (or Set me up's old `set_me_up_measure`) counts as one day
and expires like any other. Pure functions on the config dict, so a test can use a stand-in clock.
"""
import datetime
from typing import Optional

WINDOW_DAYS = 14


def _days(cfg: dict, today: datetime.date) -> dict:
    """The recorded days {iso date: MB}, including an older single figure and Set me up's older measurement as days of their own."""
    g = cfg.get("gpu_monitor") or {}
    out = {}
    for k, v in (g.get("days") or {}).items():
        if isinstance(v, (int, float)) and v > 0:
            out[str(k)] = int(v)
    legacy = []
    if isinstance(g.get("skyrimPeakMb"), (int, float)):
        legacy.append((g.get("skyrimPeakDate") or today.isoformat(), g["skyrimPeakMb"]))       # (a figure saved with no date, by a much older version, counts as seen today)
    m = cfg.get("set_me_up_measure")
    if isinstance(m, dict) and isinstance(m.get("mb"), (int, float)) and m.get("date"):
        legacy.append((m["date"], m["mb"]))
    for d, mb in legacy:
        if mb > 0 and d not in out:
            out[str(d)] = int(mb)
    return out


def _in_window(days: dict, today: datetime.date) -> dict:
    keep = {}
    for k, mb in days.items():
        try:
            d = datetime.date.fromisoformat(k)
        except ValueError:
            continue
        if today - datetime.timedelta(days=WINDOW_DAYS) < d <= today:
            keep[k] = mb
    return keep


def figure(cfg: dict, today: Optional[datetime.date] = None) -> tuple:
    """(MB, ISO date) of the highest reading in the last 14 days, or (None, None) when there is none (the caller then uses the quarter-of-the-card guess)."""
    today = today or datetime.date.today()
    keep = _in_window(_days(cfg, today), today)
    if not keep:
        return None, None
    day = max(keep, key=lambda k: (keep[k], k))
    return keep[day], day


def record(cfg: dict, mb: int, today: Optional[datetime.date] = None, card_mb: Optional[int] = None) -> bool:
    """Add a reading to `cfg` (changed in place; the caller saves). A reading of nothing, or bigger than the card itself, is ignored. True when it raised today's figure."""
    today = today or datetime.date.today()
    if not mb or mb <= 0 or (card_mb and mb > card_mb):
        return False
    keep = _in_window(_days(cfg, today), today)
    iso = today.isoformat()
    if mb <= keep.get(iso, 0):
        return False
    keep[iso] = int(mb)
    peak = max(keep, key=lambda k: (keep[k], k))
    g = dict(cfg.get("gpu_monitor") or {})
    g.update({"days": keep, "skyrimPeakMb": keep[peak], "skyrimPeakDate": peak})
    cfg["gpu_monitor"] = g
    cfg.pop("set_me_up_measure", None)               # folded into the days above
    return True


def clear(cfg: dict) -> None:
    cfg.pop("gpu_monitor", None)
    cfg.pop("set_me_up_measure", None)
