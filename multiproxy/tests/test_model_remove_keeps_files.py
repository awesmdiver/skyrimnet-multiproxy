"""Removing a model keeps its files until the player says "delete them" (2026-10-26): the routes and the Local models page's words."""
import httpx
import pytest

import local_models_ui


@pytest.fixture
def client(proxy_module):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=proxy_module.app), base_url="http://t", timeout=30)


@pytest.mark.asyncio
async def test_the_remove_route_keeps_the_files_unless_told_to_delete(proxy_module, client, monkeypatch):
    calls = []

    async def fake_remove(model_id, delete_files=False):
        calls.append((model_id, delete_files))

    monkeypatch.setattr(proxy_module.local_svc, "remove", fake_remove)
    await client.post("/config/local-models/remove", json={"id": "a"})
    await client.post("/config/local-models/remove", json={"id": "b", "deleteFiles": False})
    await client.post("/config/local-models/remove", json={"id": "c", "deleteFiles": "yes"})          # only a real true deletes
    await client.post("/config/local-models/remove", json={"id": "d", "deleteFiles": True})
    assert calls == [("a", False), ("b", False), ("c", False), ("d", True)]


@pytest.mark.asyncio
async def test_add_and_delete_of_a_kept_model_go_through_their_routes(proxy_module, client, monkeypatch):
    seen = []

    async def fake_add(model_id):
        seen.append(("add", model_id))

    async def fake_delete(model_id):
        seen.append(("delete", model_id))

    monkeypatch.setattr(proxy_module.local_svc, "add_kept", fake_add)
    monkeypatch.setattr(proxy_module.local_svc, "delete_kept", fake_delete)
    assert (await client.post("/config/local-models/add-kept", json={"id": "m"})).json() == {"status": "added"}
    assert (await client.post("/config/local-models/delete-kept", json={"id": "m"})).json() == {"status": "deleted"}
    assert seen == [("add", "m"), ("delete", "m")]


def test_the_page_asks_once_with_three_buttons_and_an_own_file_asks_nothing_about_deleting():
    js = open(local_models_ui.__file__, encoding="utf-8").read()
    for words in ("'Remove, keep files'", "'Remove, delete files'", "Keep them to add the model back or copy it to another PC without downloading it again.",
                  "'Your file stays where it is.'", "kept: 'On this PC'", "add: 'Add'", "delete: 'Delete'"):
        assert words in js, words
    assert "deleteFiles: answer === 'delete'" in js, "only the delete button sends a delete"
    own = js[js.index("function lmRemove"):js.index("function lmAddKept")]
    assert own.index("if (custom)") < own.index("ownAskRemove"), "a player's own file never gets the keep-or-delete question"
    assert "Delete ' + name + ' from your computer? You can download it again later." not in js, "no more one-click delete question on Remove"
    assert 'red' not in js[js.index("function ownAskRemove"):js.index("// A visible")], "no red buttons"
