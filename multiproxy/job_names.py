"""The names and descriptions of SkyrimNet's jobs, read from SkyrimNet itself (2026-10-02), so MultiProxy shows what a player sees on SkyrimNet's own AI > Models page and
never a list of our own. Nothing from MultiProxy is imported here.

Two sources, both under the SkyrimNet folder MultiProxy already locates (`Data/SKSE/Plugins/SkyrimNet`), neither of which holds a key:
  1. SkyrimNet's own jobs: its dashboard's text table, entries `"llm.variant_<key>":"<name>"` and `"llm.variant_<key>_desc":"<description>"`, in
     `assets/web-new/main.<hash>.js` (the hash changes with every SkyrimNet version, so any `main.*.js` in `assets/web-new` or `assets/web` that has the table is used;
     the newest wins). Dialogue is `llm.variant_default`; MultiProxy's own key for it is `dialogue`.
  2. Jobs other mods add: each mod's manifest, `config/plugins/<Mod>/manifest.yaml` (and `external/<author>.<mod>/settings/<Mod>.yaml`, where Beta 25 keeps them), with
     `plugin: {name, description}` and `variants: [{name, description}]`. SkyrimNet shows these by their raw name with a puzzle-piece mark.
When the files can't be found or read, a built-in copy of SkyrimNet's own twelve names and descriptions as they are today is used (so the fallback mirrors SkyrimNet too)
and everything else is shown by its raw name. Files are re-read only when they change, and a problem with one is never fatal.
"""
import glob
import os
import re
import time
from typing import Optional

SRC_SKYRIMNET, SRC_MOD, SRC_BUILTIN, SRC_RAW = "skyrimnet", "mod", "builtin", "raw"
NO_DESCRIPTION = "MultiProxy doesn't have the details for this job."
STATUS_FILES = "SkyrimNet's own files"
STATUS_BUILTIN = "a built-in copy of SkyrimNet's own names (SkyrimNet's files weren't found or couldn't be read)"
OWN_KEY_FOR = {"default": "dialogue"}                      # SkyrimNet's table calls the Dialogue job "default"
RECHECK_S = 5.0

# SkyrimNet's own twelve jobs, copied word for word from its dashboard text table (Beta 26 rc2, 2026-10-02): the fallback when its files can't be read.
BUILTIN: dict = {
    'dialogue': ('Dialogue', "Primary model for NPC conversations and any task that doesn't override a variant."),
    'combat': ('Combat', 'Handles NPC dialogue and reactions during combat encounters.'),
    'CharacterProfileGeneration': ('Character Profiles', 'Generates and updates NPC biographies and personality descriptions.'),
    'DiaryGeneration': ('Diary Generation', 'Writes diary entries summarizing NPC experiences.'),
    'memory': ('Memory', 'Processes events into long-term NPC memories with importance scoring.'),
    'gamemaster_evaluation': ('GameMaster', 'Evaluates scenes and decides when NPCs should speak or act.'),
    'action_evaluation': ('Action Evaluation', 'Determines which actions NPCs can take in response to events.'),
    'meta': ('Meta', 'Quick classification and routing decisions. Uses a fast, cheap model.'),
    'decisions': ('Decisions',
                  'Typed choice questions (NPC mood, who speaks next, who you are addressing) answered with probabilities by a decision model. Its provider must '
                  'have the Decisions or System One API style (the built-in OpenRouter (Decisions) uses your OpenRouter key). Sampling parameters do not apply. '
                  'Turn each task on or off under Conversations.'),
    'vision': ('Vision', 'Analyzes screenshots for OmniSight object and scene descriptions.'),
    'UniversalTranslator': ('Universal Translator', 'Applies speech pattern rules to transform NPC dialogue style.'),
    'AgentDefault': ('AI Assistant', 'Tool-calling agent for dashboard tasks and automation.')}


_ESC = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def unescape_js(body: str) -> str:
    """The text inside a JavaScript string literal (without its quotes) as the string it stands for: \\" \\' \\\\ \\n \\uXXXX \\xXX and the like."""
    out, i, n = [], 0, len(body)
    while i < n:
        c = body[i]
        if c != "\\" or i + 1 >= n:
            out.append(c)
            i += 1
            continue
        d = body[i + 1]
        if d == "u" and re.fullmatch(r"[0-9a-fA-F]{4}", body[i + 2:i + 6] or ""):
            cp = int(body[i + 2:i + 6], 16)
            i += 6
            if 0xD800 <= cp < 0xDC00 and body[i:i + 2] == "\\u" and re.fullmatch(r"[0-9a-fA-F]{4}", body[i + 2:i + 6] or ""):
                lo = int(body[i + 2:i + 6], 16)
                if 0xDC00 <= lo < 0xE000:
                    cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00)
                    i += 6
            out.append(chr(cp))
        elif d == "x" and re.fullmatch(r"[0-9a-fA-F]{2}", body[i + 2:i + 4] or ""):
            out.append(chr(int(body[i + 2:i + 4], 16)))
            i += 4
        else:
            out.append(_ESC.get(d, d))
            i += 2
    return "".join(out)


_ENTRY = re.compile(r'"llm\.variant_([A-Za-z0-9_]+?)(_desc)?"\s*:\s*"((?:[^"\\]|\\.)*)"')


def parse_text_table(text: str) -> dict:
    """{job key: {"name", "about"}} from a SkyrimNet dashboard script. Only jobs that have a name are returned (a description alone is not a job)."""
    names: dict = {}
    descs: dict = {}
    for m in _ENTRY.finditer(text or ""):
        key = OWN_KEY_FOR.get(m.group(1), m.group(1))
        (descs if m.group(2) else names)[key] = unescape_js(m.group(3))
    return {k: {"name": v, "about": descs.get(k, "")} for k, v in names.items()}


def parse_manifest(text: str) -> Optional[tuple]:
    """(mod name, [(variant key, description or '')]) from a mod's manifest, or None when it isn't readable YAML or has no such shape (ignored, never fatal)."""
    try:
        import yaml
        doc = yaml.safe_load(text)
    except Exception:
        return None
    if not isinstance(doc, dict):
        return None
    plugin = doc.get("plugin") if isinstance(doc.get("plugin"), dict) else {}
    mod = str(plugin.get("name") or "").strip()
    variants = []
    raw = doc.get("variants")
    for v in raw if isinstance(raw, list) else []:
        if isinstance(v, dict) and v.get("name"):
            d = v.get("description")
            variants.append((str(v["name"]).strip(), str(d).strip() if isinstance(d, str) else ""))
    if not variants and not mod:
        return None
    return mod, variants


def _sig(paths: list) -> tuple:
    out = []
    for p in paths:
        try:
            st = os.stat(p)
            out.append((p, st.st_mtime_ns, st.st_size))
        except OSError:
            pass
    return tuple(out)


class JobNames:
    """Reads the two sources (cached by file time, at most every few seconds) and answers for any job key."""

    def __init__(self, clock=time.time):
        self.clock = clock
        self.dir: Optional[str] = None
        self._at = 0.0
        self._sig: Optional[tuple] = None
        self._own: dict = {}
        self._mods: dict = {}             # variant key -> {"mod", "about"}
        self.from_files = False

    # ---- loading ----
    def refresh(self, skyrimnet_dir: Optional[str], force: bool = False) -> None:
        now = self.clock()
        if not force and skyrimnet_dir == self.dir and now - self._at < RECHECK_S:
            return
        self._at = now
        self.dir = skyrimnet_dir
        if not skyrimnet_dir or not os.path.isdir(skyrimnet_dir):
            self._sig, self._own, self._mods, self.from_files = None, {}, {}, False
            return
        scripts = []
        for sub in ("web-new", "web"):
            scripts += glob.glob(os.path.join(skyrimnet_dir, "assets", sub, "main.*.js"))
        manifests = sorted(glob.glob(os.path.join(skyrimnet_dir, "config", "plugins", "*", "manifest.yaml"))) + \
            sorted(glob.glob(os.path.join(skyrimnet_dir, "external", "*", "settings", "*.yaml")))
        sig = _sig(scripts + manifests)
        if sig == self._sig:
            return
        self._sig = sig
        own: dict = {}
        for path in sorted(scripts, key=lambda p: (-(os.stat(p).st_mtime if os.path.exists(p) else 0), p)):      # the newest dashboard first
            try:
                got = parse_text_table(open(path, encoding="utf-8", errors="replace").read())
            except OSError:
                continue
            if "dialogue" in got and len(got) > len(own):
                own = got
        mods: dict = {}
        for path in manifests:
            try:
                parsed = parse_manifest(open(path, encoding="utf-8", errors="replace").read())
            except OSError:
                continue
            if not parsed:
                continue
            mod, variants = parsed
            for key, about in variants:
                if key not in mods or (not mods[key]["about"] and about):
                    mods[key] = {"mod": mod, "about": about}
        self._own, self._mods = own, mods
        self.from_files = bool(own)

    # ---- answering ----
    def info(self, key: str) -> dict:
        """{"name", "about", "mod", "source", "puzzle"} for a job key. A SkyrimNet job: its own name and description. A mod's job: its raw name, the description the mod
        declared and which mod. Anything else: the raw name and a plain line saying no description has been seen."""
        own = self._own.get(key)
        if own:
            return {"name": own["name"], "about": own["about"], "mod": None, "source": SRC_SKYRIMNET, "puzzle": False}
        if key in self._mods and key not in BUILTIN:
            m = self._mods[key]
            return {"name": key, "about": m["about"], "mod": m["mod"] or None, "source": SRC_MOD, "puzzle": True}
        if key in BUILTIN:
            return {"name": BUILTIN[key][0], "about": BUILTIN[key][1], "mod": None, "source": SRC_BUILTIN, "puzzle": False}
        return {"name": key, "about": NO_DESCRIPTION, "mod": None, "source": SRC_RAW, "puzzle": True}

    def name(self, key: str) -> str:
        return self.info(key)["name"]

    def known_keys(self) -> list:
        """Every job key the files declare (SkyrimNet's own first, then the mods')."""
        return list(self._own) + [k for k in self._mods if k not in self._own]

    def status(self) -> dict:
        return {"source": "files" if self.from_files else "builtin", "text": STATUS_FILES if self.from_files else STATUS_BUILTIN,
                "mods": sorted({m["mod"] for m in self._mods.values() if m["mod"]})}


NAMES = JobNames()
