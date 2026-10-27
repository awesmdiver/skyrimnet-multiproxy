"""The opt-in loop-lag probe (loop_lag.py): it says where the server was blocked, costs nothing when it isn't asked for."""
import asyncio
import time

import pytest

import loop_lag

pytestmark = pytest.mark.asyncio


def blocks_the_loop():
    time.sleep(0.15)


async def test_a_stall_is_logged_with_the_code_that_caused_it():
    logs: list = []
    loop_lag.STALLS.clear()
    probe = loop_lag.LoopLagProbe(25, logs.append)
    probe.start()
    await asyncio.sleep(0.1)
    blocks_the_loop()
    await asyncio.sleep(0.2)
    await probe.stop()
    assert len(logs) == 1 and logs[0].startswith("The server was busy for ") and "blocks_the_loop" in logs[0] and "test_loop_lag.py" in logs[0]
    when, ms, where = loop_lag.STALLS[-1]
    assert ms >= 100 and "blocks_the_loop" in where


async def test_a_quiet_loop_logs_nothing():
    logs: list = []
    probe = loop_lag.LoopLagProbe(50, logs.append)
    probe.start()
    for _ in range(10):
        await asyncio.sleep(0.02)
    await probe.stop()
    assert logs == []


def test_it_is_off_unless_the_variable_is_set():
    assert loop_lag.threshold_from_env({}) is None
    assert loop_lag.threshold_from_env({loop_lag.ENV: ""}) is None
    assert loop_lag.threshold_from_env({loop_lag.ENV: "abc"}) is None
    assert loop_lag.threshold_from_env({loop_lag.ENV: "0"}) is None
    assert loop_lag.threshold_from_env({loop_lag.ENV: "25"}) == 25.0


async def test_start_from_env_starts_nothing_when_unset():
    assert loop_lag.start_from_env(lambda m: None, {}) is None
    logs: list = []
    probe = loop_lag.start_from_env(logs.append, {loop_lag.ENV: "30"})
    assert probe is not None and "30 ms" in logs[0]
    await probe.stop()
