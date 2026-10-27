"""A device is shown by its name as it was typed or found (MSI), never by its lower-case key (msi). The key stays the lookup: routes, saved settings, model prefixes."""
import json
import re

import pytest

import decision_ui
import other_pcs as op
import other_pcs_ui
import voice_ui
from tests.test_other_pcs import allow_any_host, door, make_remotes      # noqa: F401  (a paired door on 127.0.0.1)


@pytest.fixture
def msi(proxy_module):
    """The director's own case: a saved Courier device with key `msi` and name `MSI`."""
    svc = proxy_module.remote_svc
    svc._load()
    saved = dict(svc._remotes)
    svc._remotes.clear()
    svc._remotes["msi"] = op.Remote(id="msi", kind="courier", name="MSI", base="http://192.168.1.50:8110", online=True)
    yield svc._remotes["msi"]
    svc._remotes.clear()
    svc._remotes.update(saved)


class TestWhatTheScreensAreGiven:
    def test_the_list_carries_the_name_and_the_key(self, test_client, msi):
        row = test_client.get("/config/other-pcs").json()["servers"][0]
        assert row["id"] == "msi" and row["name"] == "MSI"

    def test_the_other_pcs_script_shows_the_name_everywhere_but_in_element_keys(self):
        js = other_pcs_ui.OTHER_PCS_JS if hasattr(other_pcs_ui, "OTHER_PCS_JS") else "".join(v for v in vars(other_pcs_ui).values() if isinstance(v, str))
        assert "function opName(r)" in js and "(r && r.name) || (r && r.id)" in js
        shown = [m.group(0) for m in re.finditer(r"(?<!_' \+ )esc\((?:r\.id|id)\)", js)]
        assert shown == [], shown                     # every esc(id) left ends an element id (opFindExtra_..., opDl_..., opDlList_..., opDlGo_..., opDlNote_...), never text
        assert len(re.findall(r"_' \+ esc\((?:r\.id|id)\)", js)) >= 2
        for needle in ("opRemove(\\'' + jsq(r.id) + '\\', \\'' + jsq(opName(r)) + '\\')", "opCNewCode(\\'' + jsq(r.id) + '\\', \\'' + jsq(opName(r)) + '\\')",
                       "esc(opName(r)) + '</span>'"):
            assert needle in js, needle

    def test_the_decision_and_voice_cards_show_the_name(self):
        for js, helper in ((decision_ui.DECISION_JS, "dcShowPc"), (voice_ui.VOICE_JS if hasattr(voice_ui, "VOICE_JS") else "".join(v for v in vars(voice_ui).values() if isinstance(v, str)), "vcShowPc")):
            assert f"function {helper}(k)" in js and "(p && p.name) || k" in js
            assert "name: p.id," not in js and "label(p.id," not in js

    def test_the_decision_status_and_the_memory_picker_name_it(self, test_client, msi, proxy_module):
        wired = proxy_module.decision_handles.remote.remotes            # (the decision card holds the list the app started with)
        wired._load()
        wired._remotes["msi"] = msi
        try:
            assert [p["name"] for p in test_client.get("/config/decisions/status").json()["pcs"] if p["id"] == "msi"] == ["MSI"]
        finally:
            wired._remotes.pop("msi", None)
        pcs = test_client.get("/config/local-models/memory").json()["pcs"]
        assert [p["label"] for p in pcs if p["id"] == "msi"] == ["MSI"]

    def test_the_speeds_filter_and_the_backup_options_name_it(self, test_client, msi, proxy_module, monkeypatch):
        monkeypatch.setattr(proxy_module, "ENABLE_SPEED_TRACKING", True)
        opts = test_client.get("/stats/speeds/pcs").json()["options"]
        assert {"id": "msi", "label": "MSI"} in opts


class TestOverview:
    def test_the_machine_row_and_the_line_say_msi_not_msi_lower_case(self, test_client, msi):
        msi.online = False
        data = test_client.get("/stats/overview").json()
        names = [m["name"] for m in data["machines"]]
        assert "MSI" in names and "msi" not in names
        assert "MSI" in [a["name"] for a in data["attention"]] and "msi" not in [a["name"] for a in data["attention"]]


class TestTheNameKeepsItsCase:
    @pytest.mark.asyncio
    async def test_a_typed_nickname_keeps_its_case_and_the_key_is_its_slug(self, door, allow_any_host):
        store = {}
        remotes = make_remotes(store)
        r = await remotes.add_multiproxy(f"127.0.0.1:{door.port}", door.code, "Den PC!")
        assert r.id == "den-pc" and r.name == "Den PC!" and r.label == "Den PC!"
        await remotes._poll(r)
        assert r.name == "Den PC!"                      # the device's own name doesn't replace what was typed
        assert store["remote_servers"][0]["name"] == "Den PC!" and store["remote_servers"][0]["name_typed"] is True

    @pytest.mark.asyncio
    async def test_a_found_pc_keeps_the_name_it_reports_for_itself(self, door, allow_any_host):
        r = await make_remotes({}).add_multiproxy(f"127.0.0.1:{door.port}", door.code, "")
        assert r.name == op.short_hostname() and r.id == op.slugify(r.name) and r.name_typed is False

    def test_an_old_saved_entry_keeps_the_name_it_already_holds_and_one_without_a_name_shows_its_key(self):
        store = {"remote_servers": [{"id": "msi", "kind": "courier", "name": "MSI", "base": "http://192.168.1.50:8110"},
                                    {"id": "den", "kind": "openai", "base": "http://192.168.1.51:8080"}]}
        remotes = make_remotes(store)
        assert remotes.get("msi").label == "MSI" and remotes.get("den").label == "den"
        remotes._persist()
        assert [r["name"] for r in store["remote_servers"]] == ["MSI", "den"]

    @pytest.mark.asyncio
    async def test_two_pcs_with_the_same_name_in_different_case_are_told_apart_by_their_keys(self, door, allow_any_host):
        remotes = make_remotes({})
        await remotes.add_multiproxy(f"127.0.0.1:{door.port}", door.code, "Den")
        with pytest.raises(op.OtherPcsError):
            await remotes.add_multiproxy(f"127.0.0.1:{door.port}", door.code, "DEN")      # the same key: refused as before


class TestTheKeyStaysTheKey:
    def test_lookups_and_routes_still_take_the_lower_case_key(self, test_client, proxy_module, msi):
        svc = proxy_module.remote_svc
        assert svc.get("msi") is msi and svc.get("MSI") is msi and svc.match("msi/some-model")[0] is msi
        r = test_client.post("/config/other-pcs/servers/remove", json={"id": "msi"})
        assert r.status_code == 200 and svc.get("msi") is None
