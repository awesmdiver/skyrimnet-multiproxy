"""Ready-made "paste a key" sources with a small free allowance (2026-10-06): Mistral, Groq and Cloudflare Workers AI.

Each one is plain data here (an address, a prefix, what to show, which settings it takes); `multiproxy.py` loops over `SOURCES` wherever it used to name one provider, so adding a
fourth is a new entry, not a new copy of forty places. They all speak the OpenAI shape, so the existing shared call code does the work.

What was checked and when (read 2026-10-06; this field changes weekly, so re-check before trusting a number):
  * Mistral: chat at https://api.mistral.ai/v1/chat/completions; the free plan exists, is small, and Mistral's own help page says it "may use your data (input and output) to train" its
    models on the free plan unless you opt out (help.mistral.ai article 347617, dated 2026-08-31). Model names from docs.mistral.ai's model overview.
  * Groq: https://api.groq.com/openai/v1; free limits per model are 30 requests a minute but only 8,000 tokens a minute and 200,000 a day (console.groq.com/docs/rate-limits), so a busy
    scene runs out in a minute or two. Its model list changes often (eight models were shut down this summer): the list is read live, the three below are only the fallback.
  * Cloudflare Workers AI: https://api.cloudflare.com/client/v4/accounts/<account id>/ai/v1; 10,000 "neurons" a day free (developers.cloudflare.com/workers-ai/platform/pricing/), which is
    a few hundred replies a day on the cheaper models. It needs an Account ID and a token: MultiProxy takes them in one box as `ACCOUNTID:TOKEN` (its own convention; Cloudflare has
    no single key). No model list is offered on that route, so the models below are the list.
  * NVIDIA NIM is NOT here: its API Trial Terms of Service (sections 1.2 and 1.4) allow "internal testing and evaluation purposes, not in production".
"""
import re
from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True)
class KeySource:
    id: str                       # also the config.json name stem (`<id>_api_key`), the route stem (`/config/<id>-key`) and the Concurrency ini line
    pill: str                     # the short name on the picker
    name: str                     # the panel's name
    prefix: str                   # the model-name prefix, `<id>/`
    base_url: str                 # `{account}` is filled from the saved key for Cloudflare
    models_path: Optional[str]    # the live model list, or None when the service offers none on this route
    placeholder: str
    help_html: str                # one plain note under the panel title
    rows: tuple                   # the fallback / only model list: (id, name, note)
    allowed: frozenset            # the settings it takes (anything else is not sent)
    max_tokens_param: str = "max_tokens"
    console: str = ""             # where a key is made (named in "didn't accept this key")
    limit_default: int = 0        # how many requests at once by default (0 = no limit): see [Concurrency] in multiproxy.ini
    skip_words: tuple = ()        # a live-list model whose id has one of these words is not a chat model
    any_hint: str = ""            # the "type your own" shape
    account_in_key: bool = False  # the saved key is `ACCOUNTID:TOKEN`

    @property
    def config_key(self) -> str:
        return f"{self.id}_api_key"

    @property
    def ini_key(self) -> str:
        return {"mistral": "Mistral", "groq": "Groq", "cloudflare": "Cloudflare"}.get(self.id, self.id.capitalize())

    @property
    def any(self) -> str:
        return self.any_hint or f"{self.prefix}MODEL"


_BASIC = frozenset({"temperature", "top_p", "stream", "stop", "seed", "max_tokens", "max_completion_tokens", "frequency_penalty", "presence_penalty", "response_format"})

SOURCES = (
    KeySource(
        id="cloudflare", pill="Cloudflare", name="Cloudflare Workers AI (free allowance)", prefix="cloudflare/",
        base_url="https://api.cloudflare.com/client/v4/accounts/{account}/ai/v1", models_path=None,
        placeholder="Paste your Account ID, a colon, then your token",
        help_html='Open <a href="https://dash.cloudflare.com" target="_blank">dash.cloudflare.com</a>, go to Workers AI, press Use REST API, and make a token. '
                  'Paste your Account ID, a colon (:) and the token in the box. The free allowance is a few hundred replies a day. (More in Help)',
        rows=(("cloudflare/@cf/qwen/qwen3-30b-a3b-fp8", "Qwen3 30B", "Good value: about 400 replies a day"),
              ("cloudflare/@cf/meta/llama-3.3-70b-instruct-fp8-fast", "Llama 3.3 70B", "The strongest: about 70 replies a day"),
              ("cloudflare/@cf/openai/gpt-oss-120b", "GPT-OSS 120B", "Big: about 85 replies a day"),
              ("cloudflare/@cf/openai/gpt-oss-20b", "GPT-OSS 20B", "About 160 replies a day"),
              ("cloudflare/@cf/mistralai/mistral-small-3.1-24b-instruct", "Mistral Small 3.1", "About 90 replies a day"),
              ("cloudflare/@cf/meta/llama-3.1-8b-instruct-fp8", "Llama 3.1 8B", "About 200 replies a day"),
              ("cloudflare/@cf/ibm-granite/granite-4.0-h-micro", "Granite 4.0 Micro", "Small, but about 1,300 replies a day")),
        allowed=frozenset({"temperature", "top_p", "stream", "seed", "max_tokens", "frequency_penalty", "presence_penalty", "response_format"}),
        console="dash.cloudflare.com", any_hint="cloudflare/@cf/MAKER/MODEL", account_in_key=True,
    ),
    KeySource(
        id="mistral", pill="Mistral", name="Mistral (free allowance)", prefix="mistral/", base_url="https://api.mistral.ai/v1", models_path="/models",
        placeholder="Paste your Mistral key",
        help_html='Get a key at <a href="https://console.mistral.ai" target="_blank">console.mistral.ai</a>. The free plan is small, and Mistral may use what you send to train its AI '
                  'unless you switch that off in its Privacy settings. (More in Help)',
        rows=(("mistral/mistral-small-2603", "Mistral Small 4", "Fast and cheap: try this one first"),
              ("mistral/mistral-large-2512", "Mistral Large 3", "The strongest, a bit slower"),
              ("mistral/ministral-3-14b-2512", "Ministral 14B", "Small and quick"),
              ("mistral/ministral-3-8b-2512", "Ministral 8B", "Smaller and quicker")),
        allowed=frozenset({"temperature", "top_p", "stream", "stop", "max_tokens", "frequency_penalty", "presence_penalty", "response_format"}),    # (no seed: Mistral calls it random_seed)
        console="console.mistral.ai", limit_default=2, skip_words=("embed", "moderation", "ocr", "voxtral", "transcri", "tts"),
    ),
    KeySource(
        id="groq", pill="Groq", name="Groq (free allowance)", prefix="groq/", base_url="https://api.groq.com/openai/v1", models_path="/models",
        placeholder="Paste your Groq key",
        help_html='Get a key at <a href="https://console.groq.com/keys" target="_blank">console.groq.com</a>. The free allowance is very small, so a busy scene runs out within minutes. (More in Help)',
        rows=(("groq/openai/gpt-oss-120b", "GPT-OSS 120B", "The biggest on Groq; it thinks before it speaks"),
              ("groq/openai/gpt-oss-20b", "GPT-OSS 20B", "Fast, good for short lines"),
              ("groq/qwen/qwen3.8-27b", "Qwen3.8 27B", "A preview: it may disappear")),
        allowed=_BASIC - {"max_tokens"}, max_tokens_param="max_completion_tokens",
        console="console.groq.com", limit_default=2, skip_words=("whisper", "orpheus", "guard", "safeguard", "tts", "compound", "embed"),
    ),
)
BY_ID = {s.id: s for s in SOURCES}

_ACCOUNT_KEY = re.compile(r"^([0-9A-Fa-f]{32}):(\S{8,})$")
ACCOUNT_KEY_WRONG = "That doesn't look right. Paste your Account ID, a colon (:), then your token, with no spaces."


def match(model: str) -> Optional[KeySource]:
    low = str(model or "").lower()
    return next((s for s in SOURCES if low.startswith(s.prefix)), None)


def strip_prefix(model: str) -> str:
    src = match(model)
    return model[len(src.prefix):] if src else model


def split_key(src: KeySource, saved: Optional[str]) -> tuple:
    """(address to call, the bearer token) for a saved key. Cloudflare's box holds `ACCOUNTID:TOKEN`; the others are just the key. A key that can't be split gives a bad address,
    which the service itself then refuses: nothing here can send a key anywhere but that service's own address."""
    if not src.account_in_key:
        return src.base_url, saved
    m = _ACCOUNT_KEY.match(saved or "")
    if not m:
        return src.base_url.format(account="invalid"), saved
    return src.base_url.format(account=m.group(1)), m.group(2)


def key_problem(src: KeySource, key: str) -> Optional[str]:
    """A plain reason a pasted key can't be right (checked before it is saved), or None."""
    if src.account_in_key and not _ACCOUNT_KEY.match(key):
        return ACCOUNT_KEY_WRONG
    return None


def model_row(src: KeySource, entry: dict) -> Optional[list]:
    """[prefix + id, name, note] for one entry of a live list, or None when it isn't a chat model."""
    name = str(entry.get("id") or "").strip()
    if not name or any(w in name.lower() for w in src.skip_words):
        return None
    return [f"{src.prefix}{name}", name, ""]
