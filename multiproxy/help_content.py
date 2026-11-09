"""The Help tab's pages (2026-10-01), built from docs/help/help-content.md so a wording change never means editing code.

`load_topics()` reads that file and returns one dict per page: id, title, group, the page as ready-to-show HTML, its plain
text (for the search box), and what its buttons do. The markdown is the small subset the file really uses: `##` groups,
`###` pages, paragraphs, `**bold**`, `code`, bullet and numbered lists, tables, a `---` line between groups, and
list items that start with the warning sign (shown as a callout). Everything is escaped before it becomes HTML; web
addresses in the text become links that open in the browser.
"""
import html
import json
import re
from pathlib import Path

CONTENT_PATH = Path(__file__).resolve().parent / "docs" / "help" / "help-content.md"
TESTSET_PATH = Path(__file__).resolve().parent / "benchmarks" / "testset-v1.json"      # the Benchmark test set page is drawn from this file, never from copied text

# page title -> (topic id, provider id on the Providers tab or None, tab to open instead, (button label, address) of its own site)
PAGES = {
    "How MultiProxy works": ("how-it-works", None, None, None),
    "Which AI should I use?": ("which-ai", None, None, None),
    "Claude": ("claude", "claude", None, ("Open Anthropic's install guide", "https://code.claude.com/docs/en/setup")),
    "Codex (ChatGPT)": ("codex", "codex", None, ("Open Codex on GitHub", "https://github.com/openai/codex")),
    "Google Antigravity": ("geminicli", "geminicli", None, ("Open Antigravity's site", "https://antigravity.google")),
    "Grok (SuperGrok)": ("grok", "grok", None, ("Open xAI's Grok Build page", "https://x.ai/news/grok-build-cli")),
    "GLM (z.ai)": ("glm", "glm", None, ("Open z.ai's key page", "https://z.ai/manage-apikey/apikey-list")),
    "Nano-GPT": ("nano", "nano", None, ("Open Nano-GPT's key page", "https://nano-gpt.com/api")),
    "OpenAI": ("openai", "openai", None, ("Open OpenAI's key page", "https://platform.openai.com/api-keys")),
    "DeepSeek": ("deepseek", "deepseek", None, ("Open DeepSeek's key page", "https://platform.deepseek.com/api_keys")),
    "Gemini": ("gemini", "gemini", None, ("Open Google AI Studio's key page", "https://aistudio.google.com/apikey")),
    "xAI (API key)": ("xai", "xai", None, ("Open xAI's key page", "https://console.x.ai")),
    "Continuum (plan key)": ("continuum", "continuum", None, ("Open Continuum's site", "https://continuumcode.ai")),
    "Kilo Gateway": ("kilo", "kilo", None, ("Open Kilo's site", "https://kilo.ai")),
    "TypeSafe (Jev)": ("typesafe", "typesafe", None, ("Open TypeSafe's site", "https://typesafe.ai")),
    "Perplexity": ("perplexity", "perplexity", None, ("Open Perplexity's key page", "https://console.perplexity.ai")),
    "Cloudflare Workers AI": ("cloudflare", "cloudflare", None, ("Open Cloudflare's dashboard", "https://dash.cloudflare.com")),
    "Mistral": ("mistral", "mistral", None, ("Open Mistral's key page", "https://console.mistral.ai")),
    "Groq": ("groq", "groq", None, ("Open Groq's key page", "https://console.groq.com/keys")),
    "Local device": ("local", "local", "local", None),
    "Set me up": ("set-me-up", None, None, None),
    "The memory guard": ("memory-guard", None, None, None),
    "Quick decisions": ("decisions", None, "decisions", None),
    "Ollama": ("ollama", "ollama", None, ("Open Ollama's download page", "https://ollama.com/download")),
    "LM Studio": ("lmstudio", "lmstudio", None, ("Open LM Studio's download page", "https://lmstudio.ai/download")),
    "Other devices": ("remote", "remote", "other", None),
    "Share a setup and Check everything": ("share-and-check", None, None, None),
    "Models": ("models", None, None, None),
    "Higgs voice": ("higgs", None, "voice", None),
    "Speech to text": ("speech-to-text", None, "voice", None),
    "Quick Test": ("quick-test", None, "quick", None),
    "Routing": ("routing", None, "routing", None),
    "Speeds": ("speeds", None, "speeds", None),
    "Benchmarks": ("benchmarks", None, "benchmarks", None),
    "Benchmark test set": ("benchmark-test-set", None, None, None),
    "Trouble?": ("trouble", None, None, None),
    "Network and ports": ("network", None, None, None),
    "Glossary": ("glossary", None, None, None),
}

# A page listed right under another in the Help list (indented)
SUB_PAGES = {"benchmark-test-set": "benchmarks"}

# Old page titles the markdown may still use -> the name the player sees (the topic id, and so its anchor, never changes)
TITLE_ALIASES = {"Local models": "Local device"}
PARTS = ("What it is", "What you need", "Set it up", "Use it in SkyrimNet", "Good to know")
HEADING_LABELS = PARTS + ("What you can do from your main device", "Setting up SkyrimNet (just once)", "Your own voice clips")
COMMAND_WORDS = {"irm", "npm", "codex", "claude", "agy", "grok", "ollama", "lms", "winget", "curl", "bash", "sh"}
PLACEHOLDER = re.compile(r"MODEL|NICKNAME|MAKER")
WARN = "⚠️"
_ADDRESS = re.compile(r"(?<![\w@/.(-])((?:[a-z0-9-]+\.)+(?:com|ai|org|google|io|dev)(?:/[A-Za-z0-9_./%?=&#-]*)?)", re.I)


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def _link(address: str) -> str:
    trail = ""
    while address and address[-1] in ".,;:!?)":
        trail = address[-1] + trail
        address = address[:-1]
    return f'<a href="https://{address}" target="_blank" rel="noopener">{address}</a>{trail}'


def _inline(text: str, section: str = "") -> str:
    """One line of markdown to HTML: code (with a Copy button where it's a command or a model name), bold, links."""
    spans: list[str] = []

    def stash(m):
        code = m.group(1)
        first = code.split(" ")[0]
        if " " in code and first in COMMAND_WORDS:
            spans.append(f'<span class="hp-cmd"><span>{html.escape(code)}</span><button class="copy" data-copy="{html.escape(code, quote=True)}">Copy</button></span>')
        elif section == "Use it in SkyrimNet" and not PLACEHOLDER.search(code):
            spans.append(f'<code>{html.escape(code)}</code> <button class="copy hp-mini" data-copy="{html.escape(code, quote=True)}">Copy</button>')
        else:
            spans.append(f"<code>{html.escape(code)}</code>")
        return f"\x00{len(spans) - 1}\x00"

    t = re.sub(r"`([^`]+)`", stash, text)
    t = html.escape(t, quote=False)
    # a link to another Help page: [text](#help/topic)
    t = re.sub(r"\[([^\]]+)\]\(#help/([\w-]+)\)", lambda m: f'<a class="hp-link" onclick="helpSelect(\'{m.group(2)}\')">{m.group(1)}</a>', t)
    t = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", t)
    t = re.sub(r"(?<!\*)\*([^*\s][^*]*?)\*(?!\*)", r"<i>\1</i>", t)
    t = _ADDRESS.sub(lambda m: _link(m.group(1)), t)
    return re.sub(r"\x00(\d+)\x00", lambda m: spans[int(m.group(1))], t)


def _plain(md: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[*`|]|^[-:| ]+$", " ", md, flags=re.M)).strip()


def _render(lines: list[str]) -> str:
    """The body of one page."""
    out: list[str] = []
    callouts: list[str] = []
    section = ""
    i = 0

    def flush_callouts():
        for c in callouts:
            out.append(f'<div class="hp-callout">{c}</div>')
        callouts.clear()

    while i < len(lines):
        line = lines[i]
        s = line.strip()
        if not s or s == "---":
            i += 1
            continue
        if s.startswith("|"):                                      # a table
            rows = []
            while i < len(lines) and lines[i].strip().startswith("|"):
                rows.append([c.strip() for c in lines[i].strip().strip("|").split("|")])
                i += 1
            head, body = rows[0], [r for r in rows[1:] if not all(re.fullmatch(r":?-+:?", c) for c in r)]
            out.append("<table><tr>" + "".join(f"<th>{_inline(c)}</th>" for c in head) + "</tr>" +
                       "".join("<tr>" + "".join(f"<td>{_inline(c)}</td>" for c in r) + "</tr>" for r in body) + "</table>")
            continue
        m = re.match(r"^(-|\d+\.)\s+(.*)", s)
        if m:                                                      # a list
            ordered = m.group(1) != "-"
            items = []
            while i < len(lines):
                mm = re.match(r"^(-|\d+\.)\s+(.*)", lines[i].strip())
                if not mm:
                    break
                items.append(mm.group(2))
                i += 1
            normal = []
            for it in items:
                if it.startswith(WARN):
                    callouts.append(_inline(it, section))
                else:
                    normal.append(it)
            tag = "ol" if ordered else "ul"
            if normal:
                out.append(f"<{tag}>" + "".join(f"<li>{_inline(it, section)}</li>" for it in normal) + f"</{tag}>")
            continue
        # a paragraph: the lines up to the next blank line or block start
        para = [s]
        i += 1
        while i < len(lines):
            n = lines[i].strip()
            if not n or n == "---" or n.startswith("|") or re.match(r"^(-|\d+\.)\s+", n):
                break
            para.append(n)
            i += 1
        text = " ".join(para)
        solo = re.fullmatch(r"\*\*([^*]+?)\*\*", text)
        if solo:                                                   # a line that is only bold is a small sub-heading
            flush_callouts()
            name = solo.group(1).rstrip(":")
            section = name if name in HEADING_LABELS else ""
            out.append(f"<h3>{html.escape(name)}</h3>" if name in HEADING_LABELS else f'<h4>{html.escape(name)}</h4>')
            continue
        lab = re.match(r"^\*\*(.+?)\*\*(\s*\([^)]*\))?(:)?\s*(.*)$", text)
        label = None
        if lab:
            name = lab.group(1).rstrip(":")
            if name in HEADING_LABELS and (lab.group(3) or lab.group(1).endswith(":") or lab.group(2)):
                label = name
                suffix = (lab.group(2) or "").strip()
                rest = lab.group(4)
        if label:
            flush_callouts()
            section = label
            out.append(f"<h3>{html.escape(label)}" + (f' <span class="hp-note">{_inline(suffix)}</span>' if suffix else "") + "</h3>")
            if rest:
                out.append(f'<p class="hp-lead">{_inline(rest, section)}</p>' if label == "What it is" else f"<p>{_inline(rest, section)}</p>")
        elif text.startswith(WARN):
            callouts.append(_inline(text, section))
        else:
            out.append(f"<p>{_inline(text, section)}</p>")
    flush_callouts()
    return "\n".join(out)


TERM = re.compile(r"^\*\*(.+?)\*\*:\s*(.+)$")


def term_slug(s: str) -> str:
    return _slug(s.replace("'", "").replace("’", ""))


def _render_glossary(lines: list[str]) -> tuple[str, list[str]]:
    """The Glossary page: a plain intro, then one entry per term. Each entry has an anchor (`gl-<slug>`) for its main
    name and one for the name in brackets, so `#help/glossary/endpoint` and `#help/glossary/multiproxys-address` both land."""
    intro, entries, anchors = [], [], []
    for line in lines:
        m = TERM.match(line.strip())
        if not m:
            intro.append(line)
            continue
        head = re.match(r"^(.*?)\s*(?:\((.*)\))?$", m.group(1))
        name, alias = head.group(1), head.group(2)
        slugs = [term_slug(name)] + ([term_slug(alias)] if alias else [])
        anchors += slugs
        spots = "".join(f'<span id="gl-{x}"></span>' for x in slugs[1:])
        note = f' <span class="hp-note">({html.escape(alias)})</span>' if alias else ""
        entries.append(f'<dt id="gl-{slugs[0]}">{html.escape(name)}{note}{spots}</dt><dd>{_inline(m.group(2))}</dd>')
    return _render(intro) + '\n<dl class="hp-gl">' + "".join(entries) + "</dl>", anchors


# --- the Benchmark test set page: the real prompts, read from the test set file -------------------------------------------------
# The words around each group (the group names and their order come from the file's own `categories`; the prompts and their sizes come from the file).
BP_GROUPS = {
    "dialogue": ("Dialogue", "An NPC answers the player in character. Checks that the reply keeps the private-thought tag SkyrimNet reads, and stays in English.",
                 "One <internal_thought> block with something inside it, then spoken text; the reply reads as English"),
    "roleplay": ("Role-play", "Same as dialogue, plus a voice cue. Checks the thought tag and the exact [sighs] style cue the voice engine needs.",
                 "One <internal_thought> block with something inside it, then spoken text; the exact text [sighs] once; the reply reads as English"),
    "structured": ("Structured answer", "Asks for a small data record instead of prose. Checks for valid JSON with the keys mood, intent and line.",
                   "The whole reply is a JSON object with exactly the keys mood, intent and line, each holding some text"),
    "instruction": ("Follow an instruction", "Asks for exactly three numbered points. Checks if the model counted and formatted the list as told.",
                    "Exactly 3 lines, numbered 1. 2. and 3., with some text after each number"),
    "edge": ("Other languages", "The traveller speaks Spanish, French or German, and the NPC must reply in kind. Checks that it reads as that language.",
             "The reply reads as Spanish (short), French (medium) or German (long)"),
    "choice": ("Make a decision", "The NPC must choose and say only the decision. Checks the reply is exactly one of accept, refuse or haggle.",
               "The whole reply is exactly one of: accept, refuse, haggle"),
}
BP_LENGTHS = {"short": "as long as the shortest quarter of one player's real SkyrimNet requests", "medium": "as long as the typical one of one player's real SkyrimNet requests",
              "long": "as long as the longest tenth of one player's real SkyrimNet requests"}
BP_ENDED = "the reply ended on its own"
MARK_PROMPTS, MARK_FINGERPRINT = "{{benchmark-prompts}}", "{{benchmark-fingerprint}}"


def _testset(path=None):
    try:
        data = json.loads(Path(path or TESTSET_PATH).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) and isinstance(data.get("prompts"), list) else None
    except (OSError, ValueError):
        return None


def _bp_name(cat: str) -> str:
    return BP_GROUPS.get(cat, (cat.replace("-", " ").capitalize(),))[0]


def _bp_text(m: dict) -> str:
    return str(m.get("content", ""))


def benchmark_prompts_html(ts) -> str:
    """The six groups of prompts, collapsed, then each prompt collapsed, with the full system and user messages. Every character from the file is escaped."""
    e = lambda x: html.escape(str(x), quote=True)
    if not ts:
        return "<p>The test set file couldn't be read.</p>"
    out = []
    for cat in [c for c in ts.get("categories", []) if isinstance(c, str)]:
        rows = [p for p in ts["prompts"] if isinstance(p, dict) and p.get("category") == cat]
        name, summary, checked = BP_GROUPS.get(cat, (_bp_name(cat), "", ""))
        inner = ""
        for p in rows:
            msgs = [m for m in p.get("messages", []) if isinstance(m, dict)]
            sysm = "\n\n".join(_bp_text(m) for m in msgs if m.get("role") == "system")
            userm = "\n\n".join(_bp_text(m) for m in msgs if m.get("role") != "system")
            ln, toks, mx = str(p.get("length", "")), p.get("approx_tokens"), p.get("max_tokens")
            head = f"<b>{e(ln.capitalize())}</b>"
            if isinstance(toks, int) and isinstance(mx, int):
                head += f'<span class="hp-muted">about {toks:,} tokens · reply up to {mx} tokens</span>'
            note = f'<p class="hp-muted"><b>{e(ln.capitalize())}:</b> {e(BP_LENGTHS.get(ln, ""))}. <b>Checked:</b> ' + (f"{e(checked)}; " if checked else "") + f"{e(BP_ENDED)}.</p>"
            inner += (f'<details class="hp-prow"><summary class="hp-phead"><span class="hp-car">▸</span>{head}</summary><div class="hp-pbody">{note}'
                      f'<div class="hp-pl">System</div><pre class="hp-pre">{e(sysm)}</pre><div class="hp-pl">User</div><pre class="hp-pre">{e(userm)}</pre></div></details>')
        n = len(rows)
        out.append(f'<details class="hp-grp2"><summary><b>{e(name)}</b><span class="hp-muted"> · {n} prompt{"" if n == 1 else "s"}</span>'
                   + (f'<div class="hp-gs">{e(summary)}</div>' if summary else "") + f"</summary>{inner}</details>")
    return "".join(out)


def _bp_plain(ts) -> str:
    """What the search box can find on the test set page in place of the prompts: the group names and their one-line summaries."""
    cats = [c for c in (ts or {}).get("categories", []) if isinstance(c, str)]
    return " ".join(f"{_bp_name(c)} {BP_GROUPS.get(c, ('', '', ''))[1]}" for c in cats)


def _fill_benchmark_markers(body: str, ts) -> str:
    sha = html.escape(str((ts or {}).get("sha256", "")), quote=True)
    body = body.replace(f"<p>{MARK_PROMPTS}</p>", benchmark_prompts_html(ts))
    fp = f'<p class="hp-fp"><code>{sha}</code></p>' if sha else ""
    body = re.sub(r"<p><b>Technical details:</b>\s*(.*?)</p>\s*<p>\{\{benchmark-fingerprint\}\}</p>",
                  lambda m: f'<details class="hp-tech"><summary>Technical details</summary><p>{m.group(1)}</p>{fp}</details>', body, flags=re.S)
    return body.replace(f"<p>{MARK_FINGERPRINT}</p>", fp)


def parse(md: str, testset=None) -> list[dict]:
    md = re.sub(r"<!--.*?-->", "", md, flags=re.S)
    topics: list[dict] = []
    group, group_intro, cur = "", [], None
    for line in md.splitlines():
        if line.startswith("## "):
            group = line[3:].strip().capitalize() if line[3:].isupper() else line[3:].strip()
            group_intro, cur = [], None
        elif line.startswith("### "):
            cur = {"title": line[4:].strip(), "group": group, "lines": [], "intro": group_intro}
            topics.append(cur)
        elif line.startswith("# "):
            continue
        elif cur is None:
            group_intro.append(line)
        else:
            cur["lines"].append(line)
    out = []
    for t in topics:
        t["title"] = TITLE_ALIASES.get(t["title"], t["title"])
        meta = PAGES.get(t["title"])
        if meta is None:
            continue
        tid, provider, tab, link = meta
        intro = "\n".join(t["intro"]).strip()
        anchors: list[str] = []
        body, anchors = _render_glossary(t["lines"]) if tid == "glossary" else (_render(t["lines"]), [])
        raw = "\n".join(t["lines"])
        if MARK_PROMPTS in raw or MARK_FINGERPRINT in raw:
            ts = testset if testset is not None else _testset()
            body = _fill_benchmark_markers(body, ts)
            raw = raw.replace(MARK_PROMPTS, _bp_plain(ts)).replace(MARK_FINGERPRINT, "")
        if intro and "Pay-as-you-go" in t["group"] or intro and "(a key)" in t["group"].lower():
            body = f'<p class="hp-groupnote">{_inline(intro)}</p>\n' + body
        out.append({
            "id": tid, "title": t["title"], "group": t["group"], "provider": provider, "tab": tab,
            "link": {"label": link[0], "url": link[1]} if link else None,
            "html": body, "anchors": anchors, "text": _plain(t["title"] + " " + intro + " " + raw), "parent": SUB_PAGES.get(tid),
        })
    return out


def load_topics(testset_path=None) -> list[dict]:
    try:
        return parse(CONTENT_PATH.read_text(encoding="utf-8"), _testset(testset_path) if testset_path else None)
    except OSError:
        return []
