"""The Settings switch "Don't run models on this PC" (2026-10-26): a gaming PC whose owner runs models on another PC says so once, and the screens stop suggesting local models.

Saved as `local_models_off` in the settings file (off by default). Quick decisions and Voice are their own services with their own tabs and are not affected. Models already
installed stay on disk, and models already running are only stopped after a yes. Every word of the switch lives here, in one place, for the screens that show it
(the page gets them as the `LM_OFF` constant in the Local device tab's script).
"""
import json

WORDS = {
    "title": "Don't run models on this PC",
    "hint": "Turns off local model suggestions here. Installed models stay on your PC.",
    "runsOn": "Models run on {pc}.",
    "off": "Models are switched off on this PC.",
    "hover": "Models are switched off on this PC: Settings.",
    "overview": "Models off here",
    "confirm": "These models are running: {names}. Turning this on will stop them.",
    "confirmGo": "Turn off and stop them",
    "turnOn": "Turn on",
    "setMeUpNext": ["Open Other devices", "Add a PC", "Open Providers"],
    "cloudQ": "You have a key. Run quick decisions in the cloud only?",           # Set me up's offer when the switch is on and a key is saved; the button turns on Decisions > "Cloud only"
    "cloudGo": "Use cloud AI only",
}


def is_off(cfg: dict) -> bool:
    return bool((cfg or {}).get("local_models_off"))


def js_words(name: str = "LM_OFF") -> str:
    """The words as a line of script, for the pages that draw them."""
    return f"const {name} = {json.dumps(WORDS, ensure_ascii=False)};\n"
