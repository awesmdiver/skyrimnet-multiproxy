"""What kind each model is (2026-10-05): Chat, Decisions, Vision, Embeddings or Other, for every provider whose own model list says so.

One small helper per provider turns what that provider REALLY reports into the same few words, so every list reads the same. Where a provider reports nothing,
the helper gives None and nothing is shown or filtered (a kind is never guessed from a model's name). Researched against the providers' own lists on 2026-10-05:

| Provider        | Where the kind comes from                                   | Values                                                              |
| Ollama          | POST /api/show -> `capabilities` (0.6.4 and newer)          | completion, decision, vision, embedding, tools, thinking, ...       |
| LM Studio       | GET /api/v0/models -> `type`                                | llm, vlm (sees pictures), embeddings                                |
| Nano-GPT        | GET /models?detailed=true -> `architecture.output_modalities`, `capabilities.vision` | text, decisions; vision true/false          |
| DeepSeek        | GET /models -> `output_modalities`, `input_modalities`      | text; text, image                                                   |
| Continuum       | GET /models -> `capabilities.input_modalities` only         | what goes IN, not what comes out: not enough to say Chat: nothing   |
| xAI, Gemini, OpenAI, GLM | id / owned_by (and prices) only                    | nothing                                                             |
| Claude, Codex, Grok, Antigravity, Local, other devices | no kind in their lists            | nothing                                                             |

Nothing here imports MultiProxy (tests use it on its own).
"""
from typing import Optional

CHAT, DECISIONS, VISION, EMBEDDINGS, OTHER = "Chat", "Decisions", "Vision", "Embeddings", "Other"
_ORDER = (CHAT, DECISIONS, VISION, EMBEDDINGS, OTHER)


def _ordered(found: set) -> list:
    return [k for k in _ORDER if k in found]


def from_ollama(capabilities) -> Optional[list]:
    """Ollama's `capabilities` list ("completion", "decision", "vision", "embedding", "tools", "thinking", ...). None when it did not give a list (an older Ollama)."""
    if not isinstance(capabilities, list):
        return None
    caps = {str(c).lower() for c in capabilities}
    found = set()
    if "completion" in caps:
        found.add(CHAT)
    if "decision" in caps or "decisions" in caps:
        found.add(DECISIONS)
    if "vision" in caps:
        found.add(VISION)
    if "embedding" in caps or "embeddings" in caps:
        found.add(EMBEDDINGS)
    return _ordered(found) or [OTHER]


def from_lmstudio(model_type) -> Optional[list]:
    """LM Studio's `type`: llm, vlm (also sees pictures) or embeddings. None when it gave none."""
    t = str(model_type or "").lower()
    if not t:
        return None
    if t == "llm":
        return [CHAT]
    if t == "vlm":
        return [CHAT, VISION]
    if t in ("embedding", "embeddings"):
        return [EMBEDDINGS]
    return [OTHER]


def from_nano(entry) -> Optional[list]:
    """One model of Nano-GPT's detailed list: what it answers with (`architecture.output_modalities`: text, or decisions) and whether it sees pictures."""
    if not isinstance(entry, dict):
        return None
    outs = (entry.get("architecture") or {}).get("output_modalities")
    if not isinstance(outs, list):
        return None
    out = {str(o).lower() for o in outs}
    found = set()
    if "text" in out:
        found.add(CHAT)
    if "decisions" in out:
        found.add(DECISIONS)
    if found and (entry.get("capabilities") or {}).get("vision") is True and CHAT in found:
        found.add(VISION)
    return _ordered(found) or [OTHER]


def from_deepseek(entry) -> Optional[list]:
    """One model of DeepSeek's list: `output_modalities` (text) and `input_modalities` (image)."""
    if not isinstance(entry, dict) or not isinstance(entry.get("output_modalities"), list):
        return None
    found = set()
    if "text" in {str(o).lower() for o in entry["output_modalities"]}:
        found.add(CHAT)
        if "image" in {str(i).lower() for i in entry.get("input_modalities") or []}:
            found.add(VISION)
    return _ordered(found) or [OTHER]


def words(kinds: Optional[list]) -> str:
    """The short note for a model's list line: 'Chat', 'Chat · Vision', ... ('' when nothing is known)."""
    return " · ".join(kinds) if kinds else ""


def can_chat(kinds: Optional[list]) -> bool:
    """False only when the provider said this model is not for chat. An unknown kind is offered as before."""
    return kinds is None or CHAT in kinds


def can_decide(kinds: Optional[list]) -> bool:
    """For a decisions job: a model the provider says makes decisions; an unknown kind is offered as before; an embedding model never."""
    return kinds is None or DECISIONS in kinds


class KindBook:
    """What the lists last learned, by the model id MultiProxy uses (`ollama/qwen3:8b`), so the pickers on other pages can use it without asking again."""

    def __init__(self) -> None:
        self._kinds: dict = {}

    def note(self, model_id: str, kinds: Optional[list]) -> None:
        if kinds is None:
            self._kinds.pop(model_id, None)
        else:
            self._kinds[model_id] = list(kinds)

    def forget_prefix(self, prefix: str) -> None:
        for k in [k for k in self._kinds if k.startswith(prefix)]:
            del self._kinds[k]

    def get(self, model_id: str) -> Optional[list]:
        return self._kinds.get(model_id)

    def all(self) -> dict:
        return dict(self._kinds)


BOOK = KindBook()
