"""The one way this program starts another program (2026-10-01).

Real problem (the director's laptop): a Courier runs with no console window (a tray icon), and on Windows every helper program it
started (nvidia-smi, tasklist, typeperf, lms, ollama...) got a console window of its own for a split second, so something flashed
on the screen every few seconds. Starting a program with CREATE_NO_WINDOW gives it no window at all.

So nothing in the Courier or MultiProxy calls `subprocess.run`, `subprocess.Popen` or `asyncio.create_subprocess_exec` directly:
they all go through `run`, `popen` and `create_subprocess_exec` here, which add the flag (and keep any flags the caller passes).
A test (tests/test_no_flashing_windows.py) fails if any module bypasses this, so a new call can't forget.
Only CREATE_NO_WINDOW is used, not a hidden start-up window, so a program that is meant to show its own window (LM Studio's
own app, the Courier's "This device is ready" window) still can.
"""
import asyncio
import os
import subprocess

CREATE_NO_WINDOW = 0x08000000


def _with_flags(kw: dict) -> dict:
    if os.name == "nt":
        kw = dict(kw)
        kw["creationflags"] = int(kw.get("creationflags") or 0) | CREATE_NO_WINDOW
    return kw


def run(*args, **kwargs) -> "subprocess.CompletedProcess":
    return subprocess.run(*args, **_with_flags(kwargs))


def popen(*args, **kwargs) -> "subprocess.Popen":
    return subprocess.Popen(*args, **_with_flags(kwargs))


async def create_subprocess_exec(*args, **kwargs):
    return await asyncio.create_subprocess_exec(*args, **_with_flags(kwargs))
