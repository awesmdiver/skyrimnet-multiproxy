"""The flags of llama-server for a local chat model: the launch command builder and the pasted-command-line parser (2026-10-04).

Built to design/local-model-settings-engines.md (the research: every spelling below was run against the pinned build, b11361).

  * `build_chat_command` turns a device's settings (`local_engine_settings`) into the command that starts one chat model. It always sets what
    MultiProxy controls (`-m`, `--mmproj`, `--host`, `--port`, `--alias`, `--no-webui`, `--reasoning off`) and nothing a player pastes can change
    them: the player's own extra flags go last and are cleaned first (`sanitize_extra_args`). With the defaults the command is exactly the one
    MultiProxy has always used.
  * `parse_command_line` reads a whole command line a player pasted from somewhere (a Discord post, a guide) and sorts every flag: understood
    (turned into a setting), kept as an extra flag (passed on as typed, not tested by us), refused (with the reason), not available in this build
    (with the build's own replacement named), or invalid (the engine would refuse the value).
  * `clean_environment` removes every `LLAMA_ARG_*` variable: the engine reads those too, so a variable the player set once in Windows would
    silently change every setting MultiProxy leaves out.

This build's spelling rules (all run): a flag and its value are two separate arguments (`--ctx-size 4096`, never `--ctx-size=4096` or `-c4096`),
the last of a repeated flag wins, flash attention always needs its value (bare `-fa` is refused), and an unknown flag stops the engine at once.
Nothing here imports `local_models` (it imports this): the pinned build number is passed in.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

PAGE_TOKENS = 680                       # about how many tokens one page of text takes: 32,768 tokens is about 48 pages (the screens say "pages")
CACHE_TYPES = ("f32", "f16", "bf16", "q8_0", "q4_0", "q4_1", "iq4_nl", "q5_0", "q5_1")
SIMPLE_CACHE_TYPES = ("f16", "q8_0", "q4_0")        # the only choices the screens offer: both halves of the cache the same, and fast kernels for it on every build we tested
LAYER_WORDS = ("auto", "all", "none")

# What each setting is called on the screens (the labels the parser reports with).
SETTING_LABELS = {
    "layers": "graphics card layers", "ctx": "memory per conversation", "parallel": "requests at the same time",
    "flashAttn": "flash attention", "cacheType": "smaller cache", "fitTargetMb": "memory to keep free for the game",
}

# Setting flags: every spelling a player might paste -> the setting it means.
_SETTING_FLAGS = {
    "-ngl": "layers", "--gpu-layers": "layers", "--n-gpu-layers": "layers",
    "-c": "ctx", "--ctx-size": "ctx",
    "-np": "parallel", "--parallel": "parallel",
    "-fa": "flashAttn", "--flash-attn": "flashAttn",
    "-ctk": "cacheK", "--cache-type-k": "cacheK",
    "-ctv": "cacheV", "--cache-type-v": "cacheV",
    "-fitt": "fitTargetMb", "--fit-target": "fitTargetMb",
}

# Flags MultiProxy sets itself: refused with a short reason. (flag -> reason)
_MANAGED_REASON = "MultiProxy sets this one itself."
_MANAGED = {f: _MANAGED_REASON for f in (
    "--host", "--port", "-m", "--model", "-a", "--alias", "--no-webui", "--webui", "--ui", "--no-ui",
    "-rea", "--reasoning", "--mmproj", "-mm", "--no-mmproj", "--mmproj-offload", "--no-mmproj-offload", "--mmproj-url", "-mmu")}
_MANAGED.update({f: "The graphics card has its own choice in MultiProxy." for f in ("-dev", "--device", "-mg", "--main-gpu")})
_MANAGED.update({f: "MultiProxy chooses how much the engine writes in its log." for f in ("-v", "--verbose", "-lv", "--verbosity", "--log-verbosity")})

_UNSAFE_WAY_IN = "That would open a way into or out of this device."
_UNSAFE_DOWNLOAD = "That would download a model from the internet."
_UNSAFE_MODEL = "That would change which model runs."
_UNSAFE_FILES = "That would read or write files MultiProxy didn't choose."
_UNSAFE_STOP = "That could make the model stop answering."
_UNSAFE_EXACT = {}
for _reason, _flags in (
    (_UNSAFE_WAY_IN, ("--api-key", "--api-key-file", "--ssl-key-file", "--ssl-cert-file", "--path", "--api-prefix", "--reuse-port", "--tools", "--tools-runtime",
                      "-ag", "--agent", "--mcp-servers-config", "--mcp-servers-json", "--ui-mcp-proxy", "--metrics", "--props", "--embedding", "--embeddings",
                      "--rerank", "--reranking", "--rpc")),
    (_UNSAFE_DOWNLOAD, ("-hf", "-hfr", "--hf-repo", "-hff", "--hf-file", "-hft", "--hf-token", "-mu", "--model-url", "-dr", "--docker-repo", "--offline")),
    (_UNSAFE_MODEL, ("--models-dir", "--models-preset", "--models-max", "--models-autoload", "--no-models-autoload", "-md", "--spec-draft-model", "--model-draft",
                     "--chat-template-file", "--grammar-file", "-jf", "--json-schema-file")),
    (_UNSAFE_FILES, ("--slot-save-path", "--media-path", "--log-file", "--log-prompts-dir")),
    (_UNSAFE_STOP, ("--sleep-idle-seconds",)),
):
    for _f in _flags:
        _UNSAFE_EXACT[_f] = _reason
_UNSAFE_PREFIXES = (("--cors", _UNSAFE_WAY_IN), ("--ssl", _UNSAFE_WAY_IN), ("--lora", _UNSAFE_MODEL), ("--control-vector", _UNSAFE_MODEL),
                    ("--spec-", _UNSAFE_MODEL), ("--draft", _UNSAFE_MODEL))

# Flags older guides still paste that the pinned build no longer has: flag -> (what to say, its replacement in this build or None).
_UNAVAILABLE = {
    "--no-mmap": ("The engine now spells this differently.", "-lm none"),
    "--mlock": ("The engine now spells this differently.", "-lm mmap+mlock"),
}

# Flags the research ran and that need no screen: kept as extra flags, and the checks read some of them (batch sizes, cache reuse).
_KNOWN_EXTRA = {
    "-b": "batch", "--batch-size": "batch", "-ub": "ubatch", "--ubatch-size": "ubatch", "--cache-reuse": "cache-reuse",
    "-t": "threads", "--threads": "threads", "-tb": "threads", "--threads-batch": "threads", "-lm": "load-mode", "--load-mode": "load-mode",
    "-fit": "fit", "--fit": "fit", "-fitc": "fit-ctx", "--fit-ctx": "fit-ctx", "--swa-full": "swa-full", "-cram": "cache-ram", "--cache-ram": "cache-ram",
    "-ts": "tensor-split", "--tensor-split": "tensor-split", "-sm": "split-mode", "--split-mode": "split-mode",
}

# What a bare flag (no value) turns into, for the flags where a bare form is common.
_FA_ON = {"on", "1", "true", "yes", "enabled"}
_FA_OFF = {"off", "0", "false", "no", "disabled"}


def looks_like_flag(tok: str) -> bool:
    return len(tok) > 1 and tok.startswith("-") and not re.fullmatch(r"-\d+(\.\d+)?", tok)


def tokenize(text: str) -> list:
    """Split a pasted command line into arguments: spaces and new lines separate them, "double" and 'single' quotes keep a path with spaces together
    (backslashes in a Windows path are left alone), a line ending in `^` or `\\` or a backtick continues on the next line, `--flag=value` becomes two
    arguments (the engine refuses the glued form), and a leading `llama-server` is dropped."""
    text = re.sub(r"[ \t]*[\^\\`][ \t]*\r?\n", " ", text or "")
    out, cur, quote, started = [], [], None, False
    for ch in text:
        if quote:
            if ch == quote:
                quote = None
            else:
                cur.append(ch)
        elif ch in ('"', "'"):
            quote, started = ch, True
        elif ch.isspace():
            if cur or started:
                out.append("".join(cur))
            cur, started = [], False
        else:
            cur.append(ch)
    if cur or started:
        out.append("".join(cur))
    tokens = []
    for t in out:
        m = re.match(r"^(--[A-Za-z0-9][A-Za-z0-9-]*)=(.*)$", t)
        if m:
            tokens += [m.group(1), m.group(2)]
        else:
            tokens.append(t)
    if tokens and re.sub(r"\.exe$", "", os.path.basename(tokens[0].replace("\\", "/")).lower()) == "llama-server":
        tokens = tokens[1:]
    return tokens


def _group(tokens: list) -> list:
    """[(flag, [values]), ...]; a value with no flag before it becomes ("", [value])."""
    groups: list = []
    for t in tokens:
        if looks_like_flag(t):
            groups.append((t, []))
        elif groups:
            groups[-1][1].append(t)
        else:
            groups.append(("", [t]))
    return groups


def _int(v: str) -> Optional[int]:
    try:
        return int(str(v).strip())
    except (TypeError, ValueError):
        return None


@dataclass
class ParseResult:
    settings: dict = field(default_factory=dict)       # setting -> new value (only the ones the line set)
    understood: list = field(default_factory=list)     # {flag, value, setting, label, becomes}
    extra: list = field(default_factory=list)          # tokens kept as extra flags, in order
    extra_items: list = field(default_factory=list)    # {flag, value, known, kind, tokens}
    refused: list = field(default_factory=list)        # {flag, value, reason}
    unavailable: list = field(default_factory=list)    # {flag, value, reason, replacement}
    invalid: list = field(default_factory=list)        # {flag, value, reason}

    def to_json(self) -> dict:
        return {"settings": self.settings, "understood": self.understood, "extra": self.extra, "extraItems": self.extra_items,
                "refused": self.refused, "unavailable": self.unavailable, "invalid": self.invalid}


def _layers_value(v: str):
    v = v.strip().lower()
    if v in ("auto", "-1"):
        return "auto"
    if v == "all":
        return "all"
    n = _int(v)
    if n is None or n < -1:
        return None
    if n == 0:
        return "none"
    return "all" if n >= 99 else n          # 99 and 999 are how everyone writes "everything"; a model never has that many layers


def _setting_value(setting: str, values: list, flag: str):
    """-> (value, error text). Bare flash attention means on."""
    if setting == "flashAttn":
        if not values:
            return "on", None
        v = values[0].strip().lower()
        if len(values) > 1:
            return None, "That needs one value."
        if v == "auto":
            return "auto", None
        if v in _FA_ON:
            return "on", None
        if v in _FA_OFF:
            return "off", None
        return None, f"“{values[0]}” is not on, off or auto."
    if len(values) != 1:
        return None, "That needs one value."
    raw = values[0]
    if setting == "layers":
        v = _layers_value(raw)
        return (v, None) if v is not None else (None, f"“{raw}” is not a number of layers, auto or all.")
    n = _int(raw)
    if setting == "ctx":
        if n is None or n < 0:
            return None, f"“{raw}” is not a memory size."
        if n == 0:
            return None, "0 means the model's whole trained size, which can take many gigabytes."
        return n, None
    if setting == "parallel":
        if n is None or n < 1:
            return None, "That needs a whole number from 1 up."
        return n, None
    if setting == "fitTargetMb":
        if n is None or n < 0:
            return None, "That needs a number of megabytes."
        return n, None
    if setting in ("cacheK", "cacheV"):
        v = raw.strip().lower()
        if v not in CACHE_TYPES:
            return None, f"“{raw}” is not a cache type the engine knows."
        return v, None
    return None, "Not understood."


def parse_command_line(text_or_tokens, build_ok: bool = True) -> ParseResult:
    """Sort every flag of a pasted command line (or an already split list). See the module docstring for the five groups."""
    tokens = tokenize(text_or_tokens) if isinstance(text_or_tokens, str) else [str(t) for t in text_or_tokens]
    res = ParseResult()
    cache = {"cacheK": None, "cacheV": None}
    cache_items = {}
    last_flag_of: dict = {}
    for flag, values in _group(tokens):
        value = " ".join(values)
        if not flag:
            res.invalid.append({"flag": value, "value": "", "reason": "That is not a flag, so llama-server would stop at it."})
            continue
        low = flag.lower()
        if low in _MANAGED:
            res.refused.append({"flag": flag, "value": value, "reason": _MANAGED[low]})
            continue
        unsafe = _UNSAFE_EXACT.get(low) or next((r for p, r in _UNSAFE_PREFIXES if low.startswith(p)), None)
        if unsafe is None and low.endswith("-default") and low.startswith("--"):
            unsafe = _UNSAFE_DOWNLOAD
        if unsafe:
            res.refused.append({"flag": flag, "value": value, "reason": unsafe})
            continue
        if low in _UNAVAILABLE:
            why, repl = _UNAVAILABLE[low]
            res.unavailable.append({"flag": flag, "value": value, "reason": why, "replacement": repl})
            continue
        setting = _SETTING_FLAGS.get(low)
        if setting:
            val, err = _setting_value(setting, values, flag)
            if err:
                res.invalid.append({"flag": flag, "value": value, "reason": err})
                continue
            if setting in cache:
                cache[setting] = val
                cache_items[setting] = {"flag": flag, "value": value}
                continue
            res.settings[setting] = val
            last_flag_of[setting] = {"flag": flag, "value": value, "setting": setting, "label": SETTING_LABELS[setting], "becomes": val}
            continue
        known = _KNOWN_EXTRA.get(low)
        res.extra += [flag, *values]
        res.extra_items.append({"flag": flag, "value": value, "known": bool(known), "kind": known or "", "tokens": [flag, *values]})
    k, v = cache["cacheK"], cache["cacheV"]
    if k and v and k == v and k in SIMPLE_CACHE_TYPES:
        res.settings["cacheType"] = k
        last_flag_of["cacheType"] = {"flag": f"{cache_items['cacheK']['flag']} / {cache_items['cacheV']['flag']}", "value": k, "setting": "cacheType",
                                     "label": SETTING_LABELS["cacheType"], "becomes": k}
    else:                                       # one half only, two different types, or a type the screens do not offer: passed on as typed
        for which in ("cacheK", "cacheV"):
            if cache[which]:
                it = cache_items[which]
                res.extra += [it["flag"], it["value"]]
                res.extra_items.append({"flag": it["flag"], "value": it["value"], "known": True, "kind": "cache-type", "tokens": [it["flag"], it["value"]]})
    res.understood = list(last_flag_of.values())
    return res


def sanitize_extra_args(tokens) -> list:
    """The extra flags that are safe to pass on: everything the player's tokens contain except what MultiProxy sets, what is refused, what this build no
    longer has, what the engine would refuse, and what is already a setting (the setting wins)."""
    return parse_command_line([str(t) for t in tokens or []]).extra


def extra_value(tokens, *flags) -> Optional[str]:
    """The value of the last of `flags` in a list of extra-flag tokens (None when it isn't there)."""
    found = None
    for flag, values in _group([str(t) for t in tokens or []]):
        if flag.lower() in flags and values:
            found = values[0]
    return found


def has_extra_flag(tokens, *flags) -> bool:
    return any(flag.lower() in flags for flag, _ in _group([str(t) for t in tokens or []]))


# --------------------------------------------------------------------------------------------
# The launch command
# --------------------------------------------------------------------------------------------
@dataclass
class LaunchPlan:
    cmd: list
    legacy: bool = False                  # an engine we do not know the spelling of: only the three settings every build takes
    ctx: int = 0
    parallel: int = 0
    dropped: list = field(default_factory=list)       # extra flags left out, with the reason


def build_chat_command(exe, model_path, mmproj, port: int, alias: str, settings: dict, override: Optional[dict] = None, *, gpu_engine: bool = True,
                       fit_target_mb: Optional[int] = None, skip_extras: bool = False, spelling_known: bool = True,
                       log_level: Optional[int] = None) -> LaunchPlan:
    """The command that starts one chat model. `settings` is the device's (see `local_engine_settings`), `override` that model's own `{ctx, parallel}`.

    `gpu_engine`: False for the processor-only engine (no layer flags at all). `fit_target_mb`: the resolved "memory to keep free" (used only when layers
    are Automatic). `skip_extras`: leave the player's extra flags out once ("Start without the extra flags"). `spelling_known`: False for an engine that is
    not the pinned build (a llama-server the player named): only the settings every build takes are passed, in the spelling every build takes."""
    s = settings
    ov = override or {}
    ctx = int(ov.get("ctx") or s.get("ctx") or 32768)
    parallel = int(ov.get("parallel") or s.get("parallel") or 2)
    cmd = [str(exe), "-m", str(model_path)]
    if mmproj:
        cmd += ["--mmproj", str(mmproj)]
    # `--reasoning off`: Gemma 4 "thinks" before answering by default, and that thinking used up the whole reply budget (empty replies, found on
    # the real test 2026-09-30). NPC dialogue wants the answer straight away.
    cmd += ["--host", "127.0.0.1", "--port", str(port), "-c", str(ctx), "-np", str(parallel), "--alias", str(alias), "--no-webui", "--reasoning", "off"]
    plan = LaunchPlan(cmd, legacy=not spelling_known, ctx=ctx, parallel=parallel)
    layers = s.get("layers", "all")
    if not spelling_known:
        if gpu_engine:
            cmd += ["-ngl", "999"]
        return plan
    if gpu_engine:
        if layers == "all":
            cmd += ["-ngl", "999"]
        elif layers == "none":
            cmd += ["-ngl", "0"]
        elif isinstance(layers, int):
            cmd += ["-ngl", str(layers)]
        elif layers == "auto" and fit_target_mb is not None:
            # No -ngl: llama.cpp's own fit decides how many layers go on the card and keeps this much free. The context stays OURS (-c above): left
            # unset, fit picks a huge context (81,920 on a small test model) and the first model to load would take the whole free card.
            cmd += ["--fit-target", str(int(fit_target_mb))]
    cache = s.get("cacheType", "f16")
    flash = s.get("flashAttn", "auto")
    extra_v = extra_value(s.get("extraArgs") or [], "-ctv", "--cache-type-v")
    if (cache != "f16" or (extra_v or "f16").lower() not in ("f16", "f32", "bf16")) and flash == "auto":
        flash = "on"                    # a smaller cache needs flash attention; "auto" is only on when the card supports it, so say so
    if flash in ("on", "off"):
        cmd += ["-fa", flash]
    if cache in SIMPLE_CACHE_TYPES and cache != "f16":
        cmd += ["-ctk", cache, "-ctv", cache]
    if log_level:
        cmd += ["-lv", str(log_level)]
    if not skip_extras:
        extras = parse_command_line(s.get("extraArgs") or [])
        cmd += extras.extra
        for group in (extras.refused, extras.unavailable, extras.invalid):
            plan.dropped += [f"{d['flag']} {d['value']}".strip() for d in group]
    return plan


def clean_environment(env: Optional[dict] = None, extra: Optional[dict] = None) -> dict:
    """A copy of the environment (this program's own when `env` is None) without any `LLAMA_ARG_*` variable, plus `extra`."""
    base = os.environ if env is None else env
    out = {k: v for k, v in base.items() if not k.upper().startswith("LLAMA_ARG_")}
    out.update(extra or {})
    return out


def flags_in_help(help_text: str) -> set:
    """Every flag an engine's own `--help` lists (lower case): used to check saved extra flags against an engine whose build changed. Each help line starts
    with its flags (`-c,    --ctx-size N   size of ...`); only that leading run is read, not flags a description mentions."""
    found = set()
    for line in (help_text or "").splitlines():
        toks = line.split()
        if not toks or not toks[0].startswith("-"):
            continue
        prev_comma = True
        for t in toks:
            bare = t.rstrip(",")
            if bare.startswith("-") and (prev_comma or t.startswith("-")):
                found.add(bare.split("[")[0].lower())
            elif not (bare.isupper() or bare[:1] in "[<{" or prev_comma):
                break
            prev_comma = t.endswith(",")
    return found
