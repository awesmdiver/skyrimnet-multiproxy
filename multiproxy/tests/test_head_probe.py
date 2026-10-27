"""A "HEAD /" readiness check is answered quietly, under the same local-only guards as GET /."""
import pytest
from fastapi.testclient import TestClient

import local_guard

OWN = "http://127.0.0.1:8000"


@pytest.fixture()
def guarded(proxy_module):
    local_guard.ENABLED = True
    return TestClient(proxy_module.app, base_url=OWN)


def test_head_root_is_answered_with_no_body_and_not_logged(guarded, caplog):
    with caplog.at_level("WARNING"):
        r = guarded.head("/")
    assert r.status_code == 200 and r.content == b""
    assert "[error]" not in caplog.text


def test_head_root_from_a_wrong_host_is_refused(guarded):
    assert guarded.head("/", headers={"Host": "evil.example"}).status_code == 403
    assert guarded.head("/", headers={"Origin": "https://evil.example"}).status_code == 403


def test_get_root_is_unchanged(guarded):
    r = guarded.get("/")
    assert r.status_code == 200 and "<html" in r.text.lower()


def test_other_routes_keep_their_methods(guarded):
    assert guarded.head("/health").status_code == 405
