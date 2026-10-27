"""No top-level `const`, `let`, `var` or `function` name is declared twice in a dashboard script (2026-10-26).
A merge once left a block in a UI file twice. Node refuses a repeated const/let (the page died), but a repeated `function` is silently allowed and the later one wins,
so the page looked fine and ran the wrong code. This reads each inline script of the real page and fails on any name declared twice at the top level.
"""
import re

DECL = re.compile(r"(?:async\s+)?(function\*?|const|let|var)\s+([A-Za-z_$][\w$]*)")


def top_level_declarations(src: str):
    """[(kind, name)] for declarations at brace depth 0, skipping strings, template text, comments and regex literals."""
    out, depth, i, n = [], 0, 0, len(src)
    prev_sig = ""                       # the last significant character, to tell a regex literal from a division
    while i < n:
        c = src[i]
        if c == "/" and src[i:i + 2] == "//":
            i = src.find("\n", i)
            i = n if i < 0 else i
            continue
        if c == "/" and src[i:i + 2] == "/*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c in "'\"":
            j = i + 1
            while j < n and src[j] != c:
                j += 2 if src[j] == "\\" else 1
            i, prev_sig = j + 1, "x"
            continue
        if c == "`":
            j = i + 1
            while j < n and src[j] != "`":
                if src[j] == "\\":
                    j += 2
                elif src[j:j + 2] == "${":          # an expression inside the template: skip to its closing brace
                    d, j = 1, j + 2
                    while j < n and d:
                        d += (src[j] == "{") - (src[j] == "}")
                        j += 1
                else:
                    j += 1
            i, prev_sig = j + 1, "x"
            continue
        if c == "/" and prev_sig in "(,=:[!&|?{};+-*%<>~^" or (c == "/" and prev_sig == ""):
            j = i + 1
            in_class = False
            while j < n and src[j] != "\n" and (in_class or src[j] != "/"):
                if src[j] == "\\":
                    j += 1
                elif src[j] == "[":
                    in_class = True
                elif src[j] == "]":
                    in_class = False
                j += 1
            i, prev_sig = j + 1, "x"
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
        elif depth == 0 and (c.isalpha() or c in "_$") and (i == 0 or not (src[i - 1].isalnum() or src[i - 1] in "_$.")):
            m = DECL.match(src, i)
            if m:
                out.append((m.group(1).rstrip("*"), m.group(2)))
                i = m.end()
                prev_sig = "x"
                continue
        if not c.isspace():
            prev_sig = c
        i += 1
    return out


def test_the_scanner_finds_the_names_and_ignores_the_noise():
    src = "const a = 1; function f() { const inner = 2; } // const ghost\nlet s = 'const nope'; const re = /const x/; let t = `${ {a:1}.a } let z`;"
    assert top_level_declarations(src) == [("const", "a"), ("function", "f"), ("let", "s"), ("const", "re"), ("let", "t")]


def test_no_top_level_name_is_declared_twice_in_any_dashboard_script(test_client):
    html = test_client.get("/").text
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    assert blocks
    twice = []
    for src in blocks:
        seen = {}
        for kind, name in top_level_declarations(src):
            if name in seen and not (kind == "var" and seen[name] == "var"):     # two `var`s of one name are allowed on purpose (SN_WORDS: two pages share the words)
                twice.append(name)
            seen[name] = kind
    assert not twice, f"declared twice at the top level of a dashboard script: {sorted(set(twice))}"
