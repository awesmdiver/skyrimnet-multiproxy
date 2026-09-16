# Releasing SkyrimNet MultiProxy

Two repos build one release, and they must not bleed into each other. This doc is the authority on
which one owns what, exactly which files cross the line, and which files must never cross it.

- **`skyrimnet-multiproxy-dev`** (private) — the proxy itself: `proxy.py`, its tests, its own
  `TECHNICAL.md`, and the working notes. Never published as a repo.
- **`skyrimnet-multiproxy`** (public) — what the world sees: the SKSE plugin (`src/`, the built
  `.dll`), the release zip, the public `README.md` / `TECHNICAL.md` / `RELEASE_NOTES.md`, and the
  GitHub releases.

The public repo does **not** contain `proxy.py` in its own tree. It arrives at release time through
`release-staging\` (gitignored), which is why a hand-copy step has always existed here — and why that
step is the one place a private file can leak into a published zip.

---

## What crosses from dev to public

Exactly six files, into `release-staging\`. Nothing else, ever.

| Staged file | Comes from | Notes |
| :--- | :--- | :--- |
| `proxy.py` | dev repo root | The only file that changes in most releases |
| `requirements.txt` | dev repo root | Runtime deps only — **not** `requirements-dev.txt` |
| `config.example.json` | dev repo root | The template, with placeholder keys |
| `proxy.ini.example` | dev repo root | The template |
| `start-proxy.bat` | dev repo root | |
| `LICENSE-proxy.txt` | dev repo's `LICENSE` | Renamed on copy. MIT requires galanx's and rhinos0608's notices to travel with the bundled code |

## What must never cross

Each of these has a real reason, not a tidiness reason:

| Never publish | Why |
| :--- | :--- |
| **`config.json`** | Live API keys in plaintext — OpenRouter, GLM, Nano-GPT, Gemini. It sits one character away from `config.example.json` in the same folder, so a careless glob or a tab-completion slip publishes real credentials. This is the single highest-risk file in either repo. |
| `proxy.ini` | Personal SkyrimWatcher paths. `proxy.ini.example` is the template that ships. |
| `proxy.log` | Request history from real play sessions. |
| `prompts/` | Queue, handoffs, board state — the working record, not a product. |
| `tests/`, `pytest.ini`, `requirements-dev.txt` | Development-only. A user running the release never needs them. |
| `design/`, `docs/`, dev's `CLAUDE.md` | Internal design material. |
| dev's `TECHNICAL.md` and `README.md` | The public repo has its **own** versions of both, written in a different voice for a different reader. Copying dev's over them is a real, easy mistake — they have identical filenames. |

**The rule that catches all of it:** stage by an explicit six-name manifest, never by copying a folder
or a wildcard. `build-release.ps1` refuses to build if a forbidden file is sitting in
`release-staging\`.

---

## Releasing

1. **Confirm what actually changed.** In the dev repo, read the commits since the last release tag.
   If only `proxy.py` changed, this is a proxy-only release and the SKSE plugin doesn't need
   rebuilding.
2. **Decide the plugin's own version.** `skse-project.json`'s `Version` names the plugin and the zip.
   If `src/` hasn't changed since the last tag, either rebuild the `.dll` so its reported version
   matches the new zip, or knowingly ship the plugin at its existing version — don't leave that
   accidental. Note which you did in the release notes' "Good to Know" if it could confuse anyone.
3. **Stage the six files** — `sync-release-staging.ps1`, which copies exactly the manifest above and
   refuses anything else.
4. **Build the zip** — `.\build-release.ps1`. It reads the version from `skse-project.json` and writes
   `github-releases\SkyrimNetMultiProxy-vX.Y.Z.zip`.
5. **Check the zip before it goes anywhere.** List its contents and confirm: no `config.json`, no
   `proxy.ini`, no `.log`, no `tests`, no `prompts`, one top-level `SkyrimNet MultiProxy\` folder, and
   `LICENSE-proxy.txt` present. A published zip can't be unpublished from anyone who already has it.
6. **Write the notes.** `RELEASE_NOTES.md` gets the new version's section on top, and the GitHub
   release body is the same text. Design side drafts, Gemini gets a real pass at it, the director
   approves the final wording. The header sells **one** flagship feature, not a flat list.

   **House style — a release page should look like the README.** Same icon vocabulary, so everything
   published reads as one family:

   | Section | Header |
   | :--- | :--- |
   | What changed | `## ✨ What's New` |
   | Smaller fixes | `## 🔧 Improvements & Polish` |
   | Caveats, upgrade notes | `## 📋 Good to Know` |
   | Who to thank | `## 🤝 Special Thanks` |
   | Where to report things | `## 💬 Need Help?` |

   Two more rules that go with it:

   - **A banner image at the very top**, the same way `README.md` opens with one. It lives in
     `assets/` (past the `assets/*` ignore rule, with its own `!assets/…` exception), shrunk to
     1600px wide, with the full-res original kept beside it locally. The release body references it
     by absolute `raw.githubusercontent.com` URL — a release body can't resolve a repo-relative path.
   - **No `# Heading` inside the release body.** GitHub already prints the release title above it, so
     a body-level H1 shows the same line twice. `RELEASE_NOTES.md` keeps its `# vX.Y.Z — …` heading,
     since that's what separates one version from the next in a single file; strip it when publishing.
7. **Publish** — tag, `gh release create`, attach the zip. Never on your own initiative: the director
   approves the notes and says go.
8. **Afterwards** — carry anything user-facing into the public `README.md` (its own voice, not dev's
   bullets copied across), then the Discord post, and clear the release's TODO lines.

   **GitHub and Discord are two different kinds of document.** The director's own framing (2026-09-16):
   *"github is the series of announcements — Discord is what the current build is about."*

   | | GitHub releases | The Discord post |
   | :--- | :--- | :--- |
   | What it is | One entry per version, kept forever | **One living post, replaced each release** |
   | Written for | Someone asking "what changed in this version" | Someone asking "what is this mod" |
   | Carries | Only that version's changes | Links, features, setup, requirements, ToS, credits — every time |
   | Overlap with last time | None | Most of it, and that's correct |

   So a release's notes are a changelog and stop there, while the Discord post is a full standalone
   description whose changelog block near the top is the part that actually changes. Most of that post
   being identical to last time is right, not lazy — and it should never be written as a terse "here's
   what's new" announcement.

   **The Links block carries three entries, every time** (the third added by the director on the v2.4.1
   post, 2026-09-16 — *"let's make sure we keep that going forward"*):

   ```
   - GitHub + full guide: <https://github.com/awesmdiver/skyrimnet-multiproxy>
   - Download: <https://github.com/awesmdiver/skyrimnet-multiproxy/releases/latest>
   - Issues / feature requests: <https://github.com/awesmdiver/skyrimnet-multiproxy/issues>
   ```

   The angle brackets suppress Discord's link preview. They only work with **no space inside them** —
   `<url >` renders the brackets as literal text instead, which is easy to introduce when editing the
   post by hand and easy to miss.

   It's written for **4,000 characters** (the director has Discord Nitro), not the default 2,000 — two
   posts were needlessly compressed before anyone checked. Measure the final text anyway: a copy pass
   will sail past a hard limit even when told not to, which has happened twice.

---

## Keeping the two repos honest

- Anything user-facing that lands in dev's README is a **flag**, not a publish: the public README only
  describes what a released version can actually do. A feature sitting unreleased in dev doesn't
  belong there yet.
- The public repo's own `TECHNICAL.md` describes the plugin and the release mechanics. The proxy's
  internals are documented in the dev repo's `TECHNICAL.md` and stay there.
- When the proxy gains a provider, the release notes and public README are the only public surfaces
  that need to change. If you find yourself copying anything else across, stop and re-read the table
  above.
