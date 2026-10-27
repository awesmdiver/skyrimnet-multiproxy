"""Measuring the voice relay and the file copy (run by hand: `VOICE_MEASURE=1 py -m pytest tests/manual/voice_relay_measure.py -s`).

The Courier here is real, on a spare port, reached at this device's own home-network address (not loopback), with a fake voice engine whose line takes a
fixed 50 ms. What it measures: (1) how much the relay adds to a line (the same line straight to the Courier's engine versus through MultiProxy's
voice endpoint and the Courier), and (2) how fast a big file moves from the main device to the Courier through the shared file transfer.
It needs the numbers from a real second device to say what a router adds; run it on the main device against the laptop to get those.
"""
import asyncio
import hashlib
import os
import socket
import statistics
import time

import pytest

from tests.test_voice_courier import PC, installed_rig, move_to, rig, speak  # noqa: F401
from tests.voice_helpers import make_engine

pytestmark = [pytest.mark.asyncio, pytest.mark.skipif(not os.environ.get("VOICE_MEASURE"), reason="measured by hand")]


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.168.1.1", 80))
        return s.getsockname()[0]
    finally:
        s.close()


async def test_what_the_relay_adds(rig):
    await installed_rig(rig)
    direct, relayed, adds = [], [], []
    await move_to(rig, PC)
    await speak(rig.client, "Warm up the path.")                                   # (first line builds connections)
    for i in range(30):
        t = time.perf_counter()
        await speak(rig.client, f"Measured line number {i}.")
        relayed.append((time.perf_counter() - t) * 1000)
        st = (await rig.client.get("/voice/status")).json()
        adds.append(st["relayMs"])
    import base64
    import aiohttp
    from tests.test_voice_service import wav
    ref = base64.b64encode(wav()).decode()
    for i in range(30):
        t = time.perf_counter()
        async with aiohttp.ClientSession() as s:
            async with s.post(rig.remote.client.base + "/courier/v1/voice/speak", json={"text": f"Measured line number {i}.", "ref_b64": ref},
                              headers=rig.remote.client._headers()) as r:
                await r.read()
        direct.append((time.perf_counter() - t) * 1000)
    print(f"\nRELAY: lines through MultiProxy's voice endpoint and the Courier: median {statistics.median(relayed):.0f} ms; "
          f"straight to the Courier's door: median {statistics.median(direct):.0f} ms; "
          f"what the relay layer itself adds (its own timing): median {statistics.median(a for a in adds if a is not None):.0f} ms")


async def test_how_fast_a_big_file_copies(rig, tmp_path):
    import file_transfer
    size = 400 * 1024 * 1024
    path = tmp_path / "big.bin"
    with open(path, "wb") as f:
        for _ in range(size // (8 * 1024 * 1024)):
            f.write(os.urandom(8 * 1024 * 1024))
    sha = file_transfer.sha256_file(path)
    spec = next(iter(rig.c_engine.builds.values())).files[0]
    # (the Courier only takes its pinned files, so time the transfer machinery with a model-kind file instead)
    from voice_engine import LICENSE_VERSION  # noqa: F401
    client = rig.remote.client
    t = time.perf_counter()
    await file_transfer.send_file(client, {"kind": "model", "model": "measure", "file": "big.gguf", "role": "model", "sha256": sha, "size": size}, path,
                                  lambda *a: None, lambda: False)
    secs = time.perf_counter() - t
    print(f"\nCOPY: {size / 1e6:.0f} MB in {secs:.1f} s = {size / 1e6 / secs:.0f} MB/s (includes the receiver's checksum pass)")
