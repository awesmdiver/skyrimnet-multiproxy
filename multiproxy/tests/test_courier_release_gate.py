"""The release gate: a release build of the Courier must not still trust the public TEST key.

Skipped in a normal test run (the repository carries the test key until the director adds the real one). The release step runs it as
    COURIER_RELEASE=1 py -m pytest tests/test_courier_release_gate.py
and a failure here means: put the real public key in courier_keys.py first (docs/RELEASING-THE-COURIER.md).
"""
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import courier_keys  # noqa: E402

release = pytest.mark.skipif(os.environ.get("COURIER_RELEASE") != "1", reason="only for a release build (COURIER_RELEASE=1)")


@release
def test_a_release_build_does_not_trust_the_test_key():
    assert courier_keys.CURRENT != courier_keys.TEST_PUBLIC_KEY, "courier_keys.CURRENT is still the public TEST key"
    assert courier_keys.TEST_PUBLIC_KEY not in courier_keys.NEXT
    assert len(courier_keys.decode_key(courier_keys.CURRENT)) == 32


def test_the_gate_itself_notices_the_test_key(monkeypatch):
    monkeypatch.setattr(courier_keys, "CURRENT", courier_keys.TEST_PUBLIC_KEY)
    assert courier_keys.is_test_key()
    monkeypatch.setattr(courier_keys, "CURRENT", courier_keys.TEST_PUBLIC_KEY[:-2] + "A=")
    assert not courier_keys.is_test_key()
