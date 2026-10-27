"""The one-line description every tab opens with (2026-10-03).

Each tab's own fixed page starts with one or two short plain sentences saying what the tab is for (DESIGN.md, "Simple is the word"), in one shared style (`.tab-desc`,
muted, the card's full width, the theme's variables). It sits in the tab's fixed markup, never inside an area a script rebuilds, so it is there on first paint and
stays. It is not a Start here box: those can be hidden, this can't. The wording is the director's final Gemini-pass text; one place for all nine.
"""
from html import escape

TEXT = {
    "setup": "Pick the AI services you want to use.",
    "local": "Download and run free AI models on this device, no accounts needed.",
    "other": "Borrow the graphics card of another computer in your house to share the heavy lifting.",
    "voice": "Bring your NPCs to life with real spoken voices, generated right here at home.",
    "models": "View every downloaded model across your PCs, see where each is used, and remove the ones you no longer need.",
    "routing": "Choose which AI handles each conversation or background job, and who takes over when they're busy.",
    "quick": "Chat with any model to make sure it's answering nicely before you load up the game.",
    "speeds": "Check how fast and reliably your models answer, and see how big SkyrimNet's prompts are so you can set each model's context.",
    "help": "Plain, friendly step-by-step guides for every part of MultiProxy.",
    "settings": "Adjust how MultiProxy looks, how it starts with Skyrim, and how it updates.",
}

CSS = r"""
  /* The description every tab opens with (2026-10-03) */
  .tab-desc { color:var(--muted); font-size:0.9rem; line-height:1.5; margin:0 0 14px }
"""


def html(key: str) -> str:
    """The description paragraph for one tab (`setup`, `local`, `other`, `voice`, `routing`, `quick`, `speeds`, `help`, `settings`)."""
    return f'<p class="tab-desc" data-tab-desc="{key}">{escape(TEXT[key])}</p>\n'
