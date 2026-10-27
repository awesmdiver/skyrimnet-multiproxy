"""What kind each model is (2026-10-05): one helper per provider turns what its own list reports into Chat, Decisions, Vision, Embeddings or Other, and says nothing where it reports nothing."""
import routing_ui
import model_kinds as mk
import pytest


@pytest.mark.parametrize("caps,expected", [
    (["completion", "tools", "thinking"], ["Chat"]),
    (["decision"], ["Decisions"]),
    (["completion", "vision", "tools"], ["Chat", "Vision"]),
    (["embedding"], ["Embeddings"]),
    (["tools"], ["Other"]),
    ([], ["Other"]),
    (None, None),
    ("completion", None),
])
def test_ollama_capabilities(caps, expected):
    assert mk.from_ollama(caps) == expected


@pytest.mark.parametrize("t,expected", [("llm", ["Chat"]), ("vlm", ["Chat", "Vision"]), ("embeddings", ["Embeddings"]), ("tts", ["Other"]), (None, None), ("", None)])
def test_lm_studio_type(t, expected):
    assert mk.from_lmstudio(t) == expected


def test_nano_gpt_reads_what_the_model_answers_with_and_whether_it_sees_pictures():
    chat = {"id": "a", "architecture": {"modality": "text->text", "output_modalities": ["text"]}, "capabilities": {"vision": False}}
    seeing = {"id": "b", "architecture": {"output_modalities": ["text"]}, "capabilities": {"vision": True}}
    deciding = {"id": "c", "architecture": {"modality": "text->decisions", "output_modalities": ["decisions"]}, "capabilities": {"vision": False}}
    assert mk.from_nano(chat) == ["Chat"] and mk.from_nano(seeing) == ["Chat", "Vision"] and mk.from_nano(deciding) == ["Decisions"]
    assert mk.from_nano({"id": "d"}) is None and mk.from_nano({"architecture": {}}) is None and mk.from_nano("x") is None


def test_deepseek_reads_its_input_and_output_kinds_only():
    assert mk.from_deepseek({"output_modalities": ["text"], "input_modalities": ["text"]}) == ["Chat"]
    assert mk.from_deepseek({"output_modalities": ["text"], "input_modalities": ["text", "image"]}) == ["Chat", "Vision"]
    assert mk.from_deepseek({"id": "x"}) is None


def test_the_words_and_what_is_offered():
    assert mk.words(["Chat", "Vision"]) == "Chat · Vision" and mk.words(None) == "" and mk.words([]) == ""
    assert mk.can_chat(None) and mk.can_chat(["Chat"]) and not mk.can_chat(["Decisions"]) and not mk.can_chat(["Embeddings"])
    assert mk.can_decide(None) and mk.can_decide(["Decisions"]) and not mk.can_decide(["Embeddings"]) and not mk.can_decide(["Chat"])


def test_the_book_remembers_forgets_a_provider_and_ignores_unknowns():
    b = mk.KindBook()
    b.note("ollama/a", ["Chat"])
    b.note("ollama/b", None)
    b.note("lmstudio/c", ["Chat", "Vision"])
    assert b.get("ollama/a") == ["Chat"] and b.get("ollama/b") is None
    b.forget_prefix("ollama/")
    assert b.all() == {"lmstudio/c": ["Chat", "Vision"]}


def test_the_pickers_leave_out_what_is_not_for_chat_and_the_page_shows_what_is_known():
    import multiproxy
    page = open(multiproxy.__file__, encoding="utf-8").read()
    assert "p.kinds[mid].includes('Chat')" in page, "Quick Test"
    assert "p.kinds.includes('Chat')" in routing_ui.JS if hasattr(routing_ui, "JS") else True
    src = open(routing_ui.__file__, encoding="utf-8").read()
    assert "(!p.kinds || p.kinds.includes('Chat'))" in src and "p.kinds.join(' · ')" in src, "the Routing picker"


def test_routing_view_carries_the_kinds_to_the_picker(proxy_module):
    import routing_view
    import inspect
    assert '"kinds": r.get("kinds")' in inspect.getsource(routing_view)
    from multiproxy import model_kinds
    model_kinds.BOOK.note("ollama/zzz", ["Embeddings"])
    assert model_kinds.BOOK.get("ollama/zzz") == ["Embeddings"]
    model_kinds.BOOK.note("ollama/zzz", None)
