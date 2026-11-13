"""The ChatGPT (Codex) card's default list shows every model Codex's own list offers (4.2.1).

History: 2026-11-12 the card said "3 models available" over an empty list (the subscription's bare ids did not match the vendor-prefixed family filter); then it showed 2 of 3
because two models of one family were collapsed to one. Now there is no trimming for the default provider: what Codex's picker shows is what the card shows, in the cache's order.
The page's own function is run here under Node.
"""
import json
import os
import shutil
import subprocess
import tempfile

import pytest

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node isn't installed")


def view(test_client, models):
    """The ids the default view would list for these rows ([id, name, note, pinned]), in order; and which of them are shown as pinned."""
    html = test_client.get("/").text
    a = html.index("function codexDefaultViewModels(models)")
    b = html.index("function renderCodexPanel(p)")
    src = html[a:b] + f"\nconst out = codexDefaultViewModels({json.dumps(models)});\nconsole.log(JSON.stringify(out.map(m => [m[0], !!m[3]])));\n"
    with tempfile.NamedTemporaryFile("w", suffix=".js", delete=False, encoding="utf-8") as f:
        f.write(src)
        path = f.name
    try:
        r = subprocess.run([NODE, path], capture_output=True, timeout=30)
    finally:
        os.unlink(path)
    assert r.returncode == 0, r.stderr.decode("utf-8", "replace")
    return json.loads(r.stdout.decode("utf-8"))


def ids(v):
    return [x[0] for x in v]


def row(mid, pinned=False):
    return [mid, mid.split("/")[-1], "", pinned]


DIRECTORS = [row("codex/gpt-6-luna"), row("codex/gpt-5.6-terra"), row("codex/gpt-5.6-luna")]


@needs_node
def test_the_directors_three_models_all_appear_in_the_caches_order(test_client):
    assert ids(view(test_client, DIRECTORS)) == ["codex/gpt-6-luna", "codex/gpt-5.6-terra", "codex/gpt-5.6-luna"]


@needs_node
def test_two_models_of_one_family_both_appear(test_client):
    assert ids(view(test_client, [row("codex/gpt-6-luna"), row("codex/gpt-5.6-luna")])) == ["codex/gpt-6-luna", "codex/gpt-5.6-luna"]


@needs_node
def test_nothing_is_trimmed_not_a_family_not_a_vendor_not_a_prefix(test_client):
    rows = [row("codex/gpt-6-astra"), row("codex/o3-mini"), row("codex/openai/gpt-5.5-astra"), row("codex/upstage/solar-pro4"), row("codex/gpt-5.5")]
    assert ids(view(test_client, rows)) == [r[0] for r in rows]


@needs_node
def test_a_pinned_model_outside_the_list_is_shown_once_after_the_list(test_client):
    rows = DIRECTORS + [row("codex/my-own-model", pinned="my-own-model")]
    got = view(test_client, rows)
    assert ids(got) == ["codex/gpt-6-luna", "codex/gpt-5.6-terra", "codex/gpt-5.6-luna", "codex/my-own-model"]
    assert got[-1][1] is True and not any(x[1] for x in got[:3])


@needs_node
def test_a_pinned_model_that_is_also_in_the_list_shows_once_as_the_pinned_row_where_the_list_has_it(test_client):
    rows = DIRECTORS + [row("codex/gpt-5.6-terra", pinned="gpt-5.6-terra")]
    got = view(test_client, rows)
    assert ids(got) == ["codex/gpt-6-luna", "codex/gpt-5.6-terra", "codex/gpt-5.6-luna"] and [x[1] for x in got] == [False, True, False]


@needs_node
def test_unpinning_leaves_the_model_in_the_list(test_client):
    pinned = DIRECTORS[:2] + [row("codex/gpt-5.6-luna", pinned="gpt-5.6-luna")]
    assert ids(view(test_client, pinned)) == ids(view(test_client, DIRECTORS))


@needs_node
def test_a_list_with_nothing_in_it_stays_empty_and_a_single_model_is_still_shown(test_client):
    assert view(test_client, []) == []
    assert ids(view(test_client, [row("codex/gpt-5.5")])) == ["codex/gpt-5.5"]           # (the never-empty fallback list is filled by the server when Codex's file cannot be read)


def test_a_saved_provider_is_shown_as_it_is_and_only_the_default_uses_the_function(test_client):
    html = test_client.get("/").text
    assert "const defaultViewModels = codexSelectedId === 'default' ? codexDefaultViewModels(active.models) : active.models;" in html
    assert html.count("function codexDefaultViewModels(") == 1


def test_the_server_side_fallback_list_is_what_fills_a_card_whose_file_cannot_be_read(proxy_module):
    assert proxy_module.CODEX_FALLBACK_MODELS, "Codex's cache missing or unreadable: the card still lists the built-in default models"
