"""Add a PC (add_a_pc.py and the Courier's courier.claim_pair_file): stand-ins only, 127.0.0.1, a spare port, no real Courier, no firewall, no real settings."""
import asyncio
import json
import logging
import os
import time

import aiohttp
import pytest

import add_a_pc
import courier
import other_pcs


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


class FakeRemotes:
    def __init__(self, fail=None):
        self.calls, self.fail = [], fail

    async def add_multiproxy(self, address, code, nickname=""):
        self.calls.append((address, code, nickname))
        if self.fail:
            raise other_pcs.OtherPcsError(502, self.fail)

        class R:
            name = nickname or "LAPTOP"
        return R()


def make(tmp_path, clock=None, remotes=None, packages=None):
    pk = packages if packages is not None else [{"os": "windows", "name": "", "url": "https://example.invalid/win"}, {"os": "linux", "name": "", "url": "https://example.invalid/lin"}]
    return add_a_pc.AddAPc(remotes or FakeRemotes(), logging.getLogger("t-add-a-pc"), program_dir=tmp_path, clock=clock or Clock(), ips_fn=lambda: ["127.0.0.1"],
                           this_pc=lambda: "DESKTOP", packages_fn=lambda: pk, allow_loopback=True, port=0, ask_first=False)


def run(coro):
    return asyncio.run(coro)


def split(url):
    base, _, q = url.partition("/add-a-pc?t=")
    return base, q


async def get(url, **kw):
    async with aiohttp.ClientSession() as s:
        async with s.get(url, allow_redirects=False, **kw) as r:
            return r.status, await r.text(), dict(r.headers)


async def post(url, body, headers=None):
    h = {"Content-Type": "application/json", "X-MultiProxy": "1", **(headers or {})}
    async with aiohttp.ClientSession() as s:
        async with s.post(url, data=json.dumps(body), headers=h) as r:
            return r.status, await r.text()


def test_a_token_pairs_once_then_not(tmp_path):
    async def go():
        remotes = FakeRemotes()
        svc = make(tmp_path, remotes=remotes)
        st = await svc.new_address()
        base, tok = split(st["url"])
        s1, body = await post(f"{base}/add-a-pc/register", {"token": tok, "port": 8110, "code": "KX7-42M", "name": "LAPTOP"})
        assert s1 == 200 and json.loads(body) == {"status": "paired", "name": "DESKTOP"}
        assert remotes.calls == [("127.0.0.1:8110", "KX7-42M", "LAPTOP")], "the address is the one the request came from"
        assert svc.state()["pairedName"] == "LAPTOP"
        s2, _ = await post(f"{base}/add-a-pc/register", {"token": tok, "port": 8110, "code": "KX7-42M", "name": "LAPTOP"})
        assert s2 == 410 and len(remotes.calls) == 1, "a used token does nothing"
        await svc.done()
        assert svc.state()["active"] is False
    run(go())


def test_the_address_expires_after_fifteen_minutes(tmp_path):
    async def go():
        clock = Clock()
        svc = make(tmp_path, clock=clock)
        st = await svc.new_address()
        base, tok = split(st["url"])
        assert st["expiresIn"] == 900 and add_a_pc.EXPIRES_SECONDS == 900
        clock.t += 899
        assert (await get(f"{base}/add-a-pc?t={tok}"))[0] == 200
        clock.t += 2
        status, text, _ = await get(f"{base}/add-a-pc?t={tok}")
        assert status == 410 and text == "This address has expired. Ask for a new one on the main PC."
        assert svc.state()["active"] is False
        await svc.done()
    run(go())


def test_a_new_address_stops_the_old_one_at_once(tmp_path):
    async def go():
        svc = make(tmp_path)
        a = await svc.new_address()
        base_a, tok_a = split(a["url"])
        b = await svc.new_address()
        base_b, tok_b = split(b["url"])
        assert tok_a != tok_b and len(tok_b) >= 40
        assert (await get(f"{base_b}/add-a-pc?t={tok_a}"))[0] == 410, "the old token is refused"
        assert (await get(f"{base_b}/add-a-pc?t={tok_b}"))[0] == 200
        await svc.done()
    run(go())


def test_nothing_is_served_without_a_live_token_or_with_a_wrong_one(tmp_path):
    async def go():
        svc = make(tmp_path)
        st = await svc.new_address()
        base, tok = split(st["url"])
        for path in ("/add-a-pc", "/add-a-pc?t=wrong", "/add-a-pc/pair-file", "/add-a-pc/pair-file?t=x", "/add-a-pc/download/windows?t=x"):
            status, text, _ = await get(base + path)
            assert status == 410 and tok not in text, path
        s, _ = await post(f"{base}/add-a-pc/register", {"token": "wrong", "port": 8110, "code": "A-1", "name": "x"})
        assert s == 410
        await svc.done()
        with pytest.raises(aiohttp.ClientError):
            await get(f"{base}/add-a-pc?t={tok}")                                          # the door is shut
    run(go())


def test_a_wrong_host_or_origin_or_missing_header_is_refused(tmp_path):
    async def go():
        remotes = FakeRemotes()
        svc = make(tmp_path, remotes=remotes)
        st = await svc.new_address()
        base, tok = split(st["url"])
        status, _, _ = await get(f"{base}/add-a-pc?t={tok}", headers={"Host": "evil.example:80"})
        assert status == 403
        status, _, h = await get(f"{base}/add-a-pc?t={tok}", headers={"Origin": "http://evil.example"})
        assert status == 403 and not any(k.lower().startswith("access-control") for k in h)
        status, _, _ = await get(f"{base}/add-a-pc?t={tok}", headers={"Sec-Fetch-Site": "cross-site"})
        assert status == 403
        body = {"token": tok, "port": 8110, "code": "A-1", "name": "x"}
        async with aiohttp.ClientSession() as s:
            async with s.post(f"{base}/add-a-pc/register", data=json.dumps(body), headers={"Content-Type": "application/json"}) as r:
                assert r.status == 403, "no X-MultiProxy header"
            async with s.post(f"{base}/add-a-pc/register", data=json.dumps(body), headers={"X-MultiProxy": "1", "Content-Type": "text/plain"}) as r:
                assert r.status == 415, "not JSON"
            async with s.post(f"{base}/add-a-pc/register", data=json.dumps(body), headers={"X-MultiProxy": "1", "Content-Type": "application/json", "Origin": "http://evil.example"}) as r:
                assert r.status == 403, "foreign Origin"
        assert remotes.calls == []
        await svc.done()
    run(go())


def test_only_home_network_callers_are_answered(tmp_path):
    async def go():
        svc = make(tmp_path)
        st = await svc.new_address()                                    # (the door binds to the stand-in address)
        svc.allow_loopback = False                                      # a caller on this very PC is not a home-network device
        base, tok = split(st["url"])
        assert (await get(f"{base}/add-a-pc?t={tok}"))[0] == 403
        await svc.done()
    run(go())


def test_the_page_offers_only_the_packages_that_exist_and_picks_the_right_one(tmp_path):
    async def go():
        only_linux = [{"os": "linux", "name": "", "url": "https://example.invalid/lin"}]
        svc = make(tmp_path, packages=only_linux)
        st = await svc.new_address()
        base, tok = split(st["url"])
        status, html, _ = await get(f"{base}/add-a-pc?t={tok}")
        assert status == 200 and "Download for Linux" in html and "Download for Windows" not in html
        assert "Add this PC to MultiProxy on DESKTOP" in html
        await svc.done()
        svc = make(tmp_path)
        st = await svc.new_address()
        base, tok = split(st["url"])
        _, html, _ = await get(f"{base}/add-a-pc?t={tok}", headers={"User-Agent": "Mozilla/5.0 (X11; Linux x86_64)"})
        assert html.index("Download for Linux") < html.index("Download for Windows")
        _, html, _ = await get(f"{base}/add-a-pc?t={tok}", headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64)"})
        assert html.index("Download for Windows") < html.index("Download for Linux")
        status, _, h = await get(f"{base}/add-a-pc/download/windows?t={tok}")
        assert status == 302 and h["Location"] == "https://example.invalid/win"
        status, text, h = await get(f"{base}/add-a-pc/pair-file?t={tok}")
        data = json.loads(text)
        assert status == 200 and data["token"] == tok and data["main"] == base and data["v"] == 1 and "courier-pair.json" in h["Content-Disposition"]
        await svc.done()
    run(go())


def test_a_local_installer_is_served_unchanged_and_the_finder_lists_only_real_files(tmp_path):
    folder = tmp_path / add_a_pc.INSTALLER_DIR_NAME
    folder.mkdir()
    exe = folder / "MultiProxy-Courier-Setup-1.20.exe"
    exe.write_bytes(b"MZ-installer-bytes")
    found = {p["os"]: p for p in add_a_pc.find_packages(tmp_path)}
    assert found["windows"]["path"] == exe and "url" in found["linux"], "a missing file falls back to the official link, never to a made-up file"

    async def go():
        svc = add_a_pc.AddAPc(FakeRemotes(), logging.getLogger("t"), program_dir=tmp_path, clock=Clock(), ips_fn=lambda: ["127.0.0.1"], allow_loopback=True, port=0, ask_first=False)
        st = await svc.new_address()
        base, tok = split(st["url"])
        async with aiohttp.ClientSession() as s:
            async with s.get(f"{base}/add-a-pc/download/windows?t={tok}") as r:
                assert r.status == 200 and await r.read() == b"MZ-installer-bytes"
        await svc.done()
    run(go())


def test_a_failed_try_is_logged_without_the_token_and_the_door_rate_limits(tmp_path, caplog):
    async def go():
        svc = make(tmp_path)
        st = await svc.new_address()
        base, tok = split(st["url"])
        with caplog.at_level(logging.INFO, logger="t-add-a-pc"):
            await get(f"{base}/add-a-pc?t=SECRET-GUESS-123", headers={})
            await get(f"{base}/add-a-pc?t={tok}", headers={"Origin": "http://evil.example"})
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "SECRET-GUESS-123" not in text and tok not in text and "no longer works" in text and "refused" in text.lower()
        codes = [(await get(f"{base}/add-a-pc?t=x"))[0] for _ in range(40)]
        assert 429 in codes
        await svc.done()
    run(go())


def test_a_pairing_that_fails_keeps_the_address_for_another_try(tmp_path):
    async def go():
        svc = make(tmp_path, remotes=FakeRemotes(fail="Couldn't reach that device."))
        st = await svc.new_address()
        base, tok = split(st["url"])
        s, text = await post(f"{base}/add-a-pc/register", {"token": tok, "port": 8110, "code": "A-1", "name": "x"})
        assert s == 502 and "Couldn't reach" in text and svc.state()["active"] is True and svc.state()["pairedName"] == ""
        await svc.done()
    run(go())


def test_the_screen_asks_for_a_warning_after_a_minute_and_no_address_says_so(tmp_path):
    async def go():
        clock = Clock()
        svc = make(tmp_path, clock=clock)
        await svc.new_address()
        assert svc.state()["warnNetwork"] is False
        clock.t += 61
        assert svc.state()["warnNetwork"] is True
        await svc.done()
        none = add_a_pc.AddAPc(FakeRemotes(), logging.getLogger("t"), program_dir=tmp_path, ips_fn=lambda: [])
        with pytest.raises(other_pcs.OtherPcsError) as e:
            await none.new_address()
        assert "home network address" in str(e.value)
    run(go())


# ---- the Courier's side: the pairing file ------------------------------------------------------------------------------------------------
class S:
    def __init__(self, paired=None):
        self.data = {"port": 8110, "code": "KX7-42M"}
        self._paired = paired

    def paired_name(self):
        return self._paired


def pair_file(folder, main="http://192.168.1.20:8120", token="t" * 43, **over):
    d = {"v": 1, "main": main, "token": token, "name": "DESKTOP", **over}
    f = folder / "courier-pair.json"
    f.write_text(json.dumps(d), encoding="utf-8")
    return f


def test_the_pairing_file_is_read_once_sent_and_deleted(tmp_path):
    f = pair_file(tmp_path)
    sent = []
    out = courier.claim_pair_file(S(), logging.getLogger("t"), folders=[tmp_path], own_ips=["192.168.1.7"], post=lambda url, body: sent.append((url, json.loads(body))) or True)
    assert out == "paired" and not f.exists()
    url, body = sent[0]
    assert url == "http://192.168.1.20:8120/add-a-pc/register"
    assert body["token"] == "t" * 43 and body["port"] == 8110 and body["code"] == "KX7-42M" and body["name"]
    assert courier.claim_pair_file(S(), logging.getLogger("t"), folders=[tmp_path], own_ips=["192.168.1.7"], post=lambda *a: pytest.fail("used twice")) == ""


def test_a_bad_old_public_or_already_paired_file_is_deleted_and_never_used(tmp_path):
    never = lambda *a: pytest.fail("must not send")
    log = logging.getLogger("t")
    for make_bad in (lambda: pair_file(tmp_path, main="http://8.8.8.8:8120"), lambda: pair_file(tmp_path, main="http://evil.example:8120"),
                     lambda: pair_file(tmp_path, token="short"), lambda: pair_file(tmp_path, v=2)):
        f = make_bad()
        assert courier.claim_pair_file(S(), log, folders=[tmp_path], own_ips=["192.168.1.7"], post=never) == "ignored" and not f.exists()
    f = pair_file(tmp_path)
    os.utime(f, (time.time() - 7200,) * 2)
    assert courier.claim_pair_file(S(), log, folders=[tmp_path], own_ips=["192.168.1.7"], post=never) == "ignored" and not f.exists()
    f = tmp_path / "courier-pair.json"
    f.write_text("{not json", encoding="utf-8")
    assert courier.claim_pair_file(S(), log, folders=[tmp_path], own_ips=["192.168.1.7"], post=never) == "ignored" and not f.exists()
    f = pair_file(tmp_path)
    assert courier.claim_pair_file(S(paired="OLD"), log, folders=[tmp_path], own_ips=["192.168.1.7"], post=never) == "ignored" and not f.exists()


def test_a_main_pc_that_does_not_answer_ends_with_the_code_way(tmp_path, monkeypatch):
    monkeypatch.setattr(courier.time, "sleep", lambda s: None)
    f = pair_file(tmp_path)
    tries = []

    def boom(url, body):
        tries.append(1)
        raise OSError("no route")
    assert courier.claim_pair_file(S(), logging.getLogger("t"), folders=[tmp_path], own_ips=["192.168.1.7"], post=boom) == "failed" and len(tries) == 3 and not f.exists()


def test_the_whole_hand_over_between_the_two_sides(tmp_path):
    """The Courier's real POST against the real door: the file's token pairs it, the main side gets the Courier's port, code and name."""
    async def go():
        remotes = FakeRemotes()
        svc = make(tmp_path, remotes=remotes)
        st = await svc.new_address()
        base, tok = split(st["url"])
        _, text, _ = await get(f"{base}/add-a-pc/pair-file?t={tok}")
        f = tmp_path / "courier-pair.json"
        f.write_text(text, encoding="utf-8")
        # the stand-in address is loopback, which the Courier's file rule (rightly) refuses, so the real POST is made with the same body the Courier builds
        data = json.loads(text)
        body = {"token": data["token"], "port": 8110, "code": "KX7-42M", "name": "LAPTOP"}
        s, _ = await post(f"{data['main']}/add-a-pc/register", body)
        assert s == 200 and remotes.calls[0][1] == "KX7-42M"
        await svc.done()
    run(go())


def test_the_old_code_flow_is_still_there():
    import other_pcs_ui
    js = other_pcs_ui.OTHER_PCS_JS
    for needle in ("Found on your network", "opPair(", "opAddByAddress()", "Have a code instead?", "/config/other-pcs/add-a-pc/new"):
        assert needle in js, needle
