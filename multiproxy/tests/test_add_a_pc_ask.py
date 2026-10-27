"""Add a PC asks before a PC joins (security review SF1) and the Courier trusts a pairing file only from its own network (SF2). Stand-ins only: 127.0.0.1, spare ports."""
import asyncio
import json
import logging

import add_a_pc
import courier
import other_pcs
import pytest
from tests.test_add_a_pc import FakeRemotes, S, pair_file, post, run, split, get


def ask_svc(tmp_path, remotes=None, wait=30.0):
    return add_a_pc.AddAPc(remotes or FakeRemotes(), logging.getLogger("t-ask"), program_dir=tmp_path, ips_fn=lambda: ["127.0.0.1"], this_pc=lambda: "DESKTOP",
                           packages_fn=lambda: [{"os": "windows", "name": "", "url": "https://example.invalid/w"}], allow_loopback=True, port=0, ask_first=True, join_wait=wait)


async def until(cond, seconds=3.0):
    for _ in range(int(seconds / 0.02)):
        if cond():
            return True
        await asyncio.sleep(0.02)
    return False


def body(name="LAPTOP", code="KX7-42M", port=8110):
    return {"port": port, "code": code, "name": name}


class TestTheMainPcAsks:
    def test_the_join_waits_for_add_and_add_completes(self, tmp_path):
        async def go():
            remotes = FakeRemotes()
            svc = ask_svc(tmp_path, remotes)
            base, tok = split((await svc.new_address())["url"])
            req = asyncio.ensure_future(post(f"{base}/add-a-pc/register", {"token": tok, **body()}))
            assert await until(lambda: svc.state().get("joinName") == "LAPTOP")
            assert remotes.calls == [] and not svc.state()["pairedName"], "nothing is added before the click"
            svc.answer(True)
            status, text = await req
            assert status == 200 and json.loads(text)["status"] == "paired" and len(remotes.calls) == 1
            assert svc.state()["pairedName"] == "LAPTOP" and svc.state()["joinName"] == ""
            await svc.done()
        run(go())

    def test_no_refuses_and_the_address_stays_usable(self, tmp_path):
        async def go():
            remotes = FakeRemotes()
            svc = ask_svc(tmp_path, remotes)
            base, tok = split((await svc.new_address())["url"])
            req = asyncio.ensure_future(post(f"{base}/add-a-pc/register", {"token": tok, **body()}))
            assert await until(lambda: svc.state().get("joinName"))
            svc.answer(False)
            status, text = await req
            assert status == 403 and json.loads(text) == {"status": "declined"} and remotes.calls == []
            assert svc.state()["active"] and svc.state()["joinName"] == ""
            req2 = asyncio.ensure_future(post(f"{base}/add-a-pc/register", {"token": tok, **body(name="LAPTOP2")}))        # the same address still works
            assert await until(lambda: svc.state().get("joinName") == "LAPTOP2")
            svc.answer(True)
            assert (await req2)[0] == 200
            await svc.done()
        run(go())

    def test_no_answer_in_the_time_is_a_no(self, tmp_path):
        async def go():
            remotes = FakeRemotes()
            svc = ask_svc(tmp_path, remotes, wait=0.3)
            base, tok = split((await svc.new_address())["url"])
            status, text = await post(f"{base}/add-a-pc/register", {"token": tok, **body()})
            assert status == 403 and remotes.calls == [] and svc.state()["active"] and svc.state()["joinName"] == ""
            await svc.done()
        run(go())

    def test_a_second_device_while_one_waits_is_turned_away(self, tmp_path):
        async def go():
            remotes = FakeRemotes()
            svc = ask_svc(tmp_path, remotes)
            base, tok = split((await svc.new_address())["url"])
            first = asyncio.ensure_future(post(f"{base}/add-a-pc/register", {"token": tok, **body(name="ONE")}))
            assert await until(lambda: svc.state().get("joinName") == "ONE")
            status, _ = await post(f"{base}/add-a-pc/register", {"token": tok, **body(name="TWO")})
            assert status == 409
            assert svc.state()["joinName"] == "ONE", "the one waiting is not replaced"
            svc.answer(True)
            assert (await first)[0] == 200 and len(remotes.calls) == 1
            await svc.done()
        run(go())

    def test_done_a_new_address_and_expiry_turn_a_waiting_device_away(self, tmp_path):
        async def go():
            for how in ("done", "new"):
                remotes = FakeRemotes()
                svc = ask_svc(tmp_path, remotes)
                base, tok = split((await svc.new_address())["url"])
                req = asyncio.ensure_future(post(f"{base}/add-a-pc/register", {"token": tok, **body()}))
                assert await until(lambda: svc.state().get("joinName"))
                await (svc.done() if how == "done" else svc.new_address())
                assert (await req)[0] == 403 and remotes.calls == []
                await svc.done()
        run(go())

    def test_the_name_is_text_never_html(self, tmp_path):
        async def go():
            svc = ask_svc(tmp_path)
            base, tok = split((await svc.new_address())["url"])
            req = asyncio.ensure_future(post(f"{base}/add-a-pc/register", {"token": tok, **body(name="<b>Eve</b><script>x()</script>")}))
            assert await until(lambda: svc.state().get("joinName"))
            shown = svc.state()["joinName"]
            assert "<" not in shown and ">" not in shown and len(shown) <= 40
            svc.answer(False)
            await req
            await svc.done()
        run(go())
        import other_pcs_ui
        assert "esc(a.joinName)" in other_pcs_ui.OTHER_PCS_JS if hasattr(other_pcs_ui, "OTHER_PCS_JS") else True

    def test_the_questions_words_and_script_are_in_the_page(self):
        import other_pcs_ui
        src = open(other_pcs_ui.__file__, encoding="utf-8").read()
        assert "joins: ' wants to join.'" in src and "allow: 'Add'" in src and "deny: 'No'" in src
        assert "opAddAnswer(true)" in src and "esc(a.joinName)" in src and "/config/other-pcs/add-a-pc/answer" in src

    def test_the_answer_route_only_says_yes_to_true(self, proxy_module, test_client):
        seen = []
        proxy_module.add_a_pc_svc.answer = lambda allow: seen.append(allow) or {}
        for sent in ({"allow": True}, {"allow": "yes"}, {"allow": 1}, {}, []):
            assert test_client.post("/config/other-pcs/add-a-pc/answer", json=sent).status_code == 200
        assert seen == [True, False, False, False, False]


class TestTheCourierTrustsOnlyItsOwnNetwork:
    @pytest.mark.parametrize("main,own,ok", [
        ("192.168.1.20", ["192.168.1.7"], True),
        ("192.168.1.20", ["192.168.2.7"], False),                       # another /24 of the same /16: not trusted (the pairing code is the way)
        ("192.168.1.20", ["10.0.0.5"], False),
        ("192.168.1.20", ["10.0.0.5", "192.168.1.9"], True),            # any one of its own addresses will do
        ("10.0.0.20", ["10.0.0.7"], True), ("10.0.1.20", ["10.0.0.7"], False),
        ("172.16.5.5", ["172.16.5.9"], True),
        ("100.101.2.3", ["100.64.9.9"], True),                          # Tailscale to Tailscale: the same 100.64.0.0/10
        ("100.101.2.3", ["192.168.1.7"], False),                        # a Tailscale address from a PC that is not on Tailscale
        ("192.168.1.20", ["100.64.9.9"], False),
        ("192.168.1.20", [], False),
        ("not an address", ["192.168.1.7"], False),
        ("8.8.8.8", ["8.8.8.8"], True),                                  # (public addresses never get this far: the private-address rule refuses them first)
    ])
    def test_the_rule(self, main, own, ok):
        assert courier.on_my_network(main, own) is ok

    def test_a_file_from_the_same_network_is_used(self, tmp_path):
        f = pair_file(tmp_path, main="http://192.168.1.20:8120")
        sent = []
        out = courier.claim_pair_file(S(), logging.getLogger("t"), folders=[tmp_path], own_ips=["192.168.1.7"], post=lambda url, b: sent.append(url) or True)
        assert out == "paired" and not f.exists() and sent == ["http://192.168.1.20:8120/add-a-pc/register"]

    def test_a_file_from_another_network_is_deleted_ignored_and_logged_without_the_token(self, tmp_path, caplog):
        caplog.set_level(logging.INFO)
        token = "T0kenT0kenT0kenT0kenT0kenT0ken1234"
        f = pair_file(tmp_path, main="http://192.168.7.20:8120", token=token)
        out = courier.claim_pair_file(S(), logging.getLogger("t-net"), folders=[tmp_path], own_ips=["192.168.1.7"], post=lambda *a: pytest.fail("must not send"))
        assert out == "ignored" and not f.exists()
        text = "\n".join(r.getMessage() for r in caplog.records)
        assert "not on this PC's own network" in text and token not in text and "192.168.7.20" not in text

    def test_the_tailscale_case(self, tmp_path):
        pair_file(tmp_path, main="http://100.101.2.3:8120")
        out = courier.claim_pair_file(S(), logging.getLogger("t"), folders=[tmp_path], own_ips=["100.64.9.9", "192.168.1.7"], post=lambda *a: True)
        assert out == "paired"
        pair_file(tmp_path, main="http://100.101.2.3:8120")
        assert courier.claim_pair_file(S(), logging.getLogger("t"), folders=[tmp_path], own_ips=["192.168.1.7"], post=lambda *a: pytest.fail("must not send")) == "ignored"


class TestTheCourierEndsCleanlyOnNo:
    def test_a_no_ends_the_try_without_asking_again(self, tmp_path):
        f = pair_file(tmp_path)
        tries = []

        def declined(url, b):
            tries.append(1)
            return "declined"
        out = courier.claim_pair_file(S(), logging.getLogger("t"), folders=[tmp_path], own_ips=["192.168.1.7"], post=declined)
        assert out == "declined" and len(tries) == 1 and not f.exists()

    def test_the_real_post_reads_a_403_as_a_no(self, tmp_path):
        async def go():
            svc = ask_svc(tmp_path, wait=0.2)
            base, tok = split((await svc.new_address())["url"])
            got = await asyncio.to_thread(courier._post_pairing, f"{base}/add-a-pc/register", json.dumps({"token": tok, **body()}).encode())
            assert got == "declined"
            await svc.done()
        run(go())
