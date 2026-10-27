"""The troubleshooting switch (2026-10-24): "Log what SkyrimNet sends", on the Settings page.

While it is on, the voice route and the speech-to-text route write one line per request that says what it was made of (names, kinds and sizes: voice_service.describe_request and
stt_capture.describe; never the sound, the words or a key). It replaces having to set MULTIPROXY_VOICE_FIELDS / MULTIPROXY_STT_FIELDS before starting the program, which still work.

The state lives in memory only, on purpose: it is off every time MultiProxy starts and it turns itself off after an hour, so nobody leaves it on by accident.
"""
import math
import os
import time

HOUR_SECONDS = 3600
clock = time.monotonic              # (a test swaps this for a fake clock)
_until = 0.0                        # the moment it turns itself off; 0 = off


def enable() -> None:
    global _until
    _until = clock() + HOUR_SECONDS


def disable() -> None:
    global _until
    _until = 0.0


def seconds_left() -> int:
    """Whole seconds before the switch turns itself off; 0 when it is off (or has run out)."""
    global _until
    if not _until:
        return 0
    left = _until - clock()
    if left <= 0:
        _until = 0.0
        return 0
    return math.ceil(left)


def switch_on() -> bool:
    return seconds_left() > 0


def wanted(env_name: str) -> bool:
    """True when this route should write its field line: the Settings switch is on, or the developer's environment variable `env_name` is set (as it always worked)."""
    return switch_on() or bool(os.environ.get(env_name))


def state() -> dict:
    return {"on": switch_on(), "secondsLeft": seconds_left()}
