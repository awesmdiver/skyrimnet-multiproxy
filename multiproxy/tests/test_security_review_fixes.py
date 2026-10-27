"""The Must-fix items of the security review before 4.0 (2026-10-06): keys and pairing codes stay out of logs; text from a provider cannot run as script."""
import json
import logging

from fastapi.testclient import TestClient

import courier

KEY = "AIza" + "SyD-fake-fake-fake-fake-fake-12345"
XAI = "xai-" + "fakefakefakefakefakefake"


def test_the_courier_log_file_never_holds_the_pairing_code(tmp_path):
    log = courier.setup_logging(tmp_path, console=False)
    log.info("Ready for your main device (pairing code KX7-42M). Found: none. Status: waiting.")
    for h in log.handlers:
        h.flush()
    text = (tmp_path / "courier.log").read_text(encoding="utf-8")
    assert "KX7-42M" not in text and "Ready for your main device" in text
    log.handlers.clear()


def test_the_courier_console_still_shows_the_code_to_the_person_at_the_pc(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(courier, "_stdout_is_a_terminal", lambda: True)          # (a person at a terminal; a service's redirected output is masked: see test_linux_packages)
    log = courier.setup_logging(tmp_path, console=True)
    log.info("Ready (pairing code KX7-42M)")
    out = capsys.readouterr().out
    log.handlers.clear()
    assert "KX7-42M" in out


def test_a_failed_key_save_never_logs_the_key(proxy_module, caplog):
    import local_guard
    local_guard.ENABLED = False
    c = TestClient(proxy_module.app)
    with caplog.at_level(logging.WARNING):
        c.post("/config/codex-providers", json={"name": "x", "api_key": KEY})          # no address: a 400
        c.post("/config/gemini-key", content=json.dumps({"key": KEY}), headers={"Content-Type": "text/plain"})
    assert KEY not in caplog.text and "AIza" not in caplog.text
    assert "hidden" in caplog.text


def test_the_wider_redaction_covers_the_other_providers_keys():
    r = lambda t: __import__("multiproxy")._redact_text(t)
    assert KEY not in r(f"bad key {KEY}") and XAI not in r(f"key {XAI}")
    assert "secretvalue123" not in r('{"api_key": "secretvalue123"}')
    assert "abcdefghijklmnop" not in r("Authorization: Bearer abcdefghijklmnop")
    assert r("a plain sentence about models") == "a plain sentence about models"


def test_text_from_a_provider_cannot_close_the_script(proxy_module):
    evil = [{"id": "x", "models": [["a')\"><img src=x onerror=alert(1)>", "</script><script>alert(2)</script>", "n"]]}]
    out = proxy_module._script_json(evil)
    assert "<" not in out and ">" not in out and "&" not in out
    assert json.loads(out.replace("\u003c", "<").replace("\u003e", ">").replace("\u0026", "&")) == evil


def test_the_dashboard_escapes_quotes_and_model_text(proxy_module):
    html = TestClient(proxy_module.app).get("/").text
    assert "&quot;" in html.split("function _escapeHtml")[1][:300]
    assert "<span class=\"mid\">${_escapeHtml(mid)}</span>" in html
    assert "copyModel(this, '${jsq(mid)}')" in html
