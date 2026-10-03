# Rename the GitHub repos and local folders to skyrimnet-multiproxy

Last step of the SkyrimNet MultiProxy rename (decided 2026-09-13). The in-code renames already
happened in two earlier items. This one moves the repos and folders themselves:

| | Old | New |
|---|---|---|
| Public launcher repo | `awesmdiver/claude-skyrimnet-proxy-launcher` | `awesmdiver/skyrimnet-multiproxy` |
| Private proxy dev repo | `awesmdiver/claude-skyrimnet-proxy` | `awesmdiver/skyrimnet-multiproxy-dev` |
| Launcher folder | `F:\Claude Workspace\skyrim-modding\claude-skyrimnet-proxy-launcher` | `...\skyrim-modding\skyrimnet-multiproxy` |
| Dev folder | `F:\Claude Workspace\skyrim-modding\claude-skyrimnet-proxy` | `...\skyrim-modding\skyrimnet-multiproxy-dev` |

## ⚠️ Run this from the workspace root, not from inside either project

Open the terminal at `F:\Claude Workspace` (Windows can't rename a folder a shell is sitting in). Close
any other terminal, editor, or Claude session that has either folder open, and make sure no proxy is
running from them.

## Preconditions — check all, stop if any fail

1. Both earlier items are committed:
   - dev repo: `Rename the proxy to SkyrimNet MultiProxy` (and the compact dashboard before it)
   - launcher repo: `Rename the launcher and its plugin to SkyrimNet MultiProxy`
2. Both working trees have nothing uncommitted except board-owned `prompts/` files (gitignored).
3. Both repos are pushed (`git status -sb` shows no ahead/behind).
4. `prompts/queue.json` in both projects has an empty `inFlight`.

If any fail, write the handoff saying which and stop.

## Steps

1. **Confirm with the director before the GitHub renames** — this is public and outward-facing. Show
   the two `gh repo rename` commands you're about to run and wait for a yes.
2. Rename on GitHub (`gh repo rename <new> -R awesmdiver/<old>`). GitHub redirects the old URLs.
3. Update each repo's `origin` remote to the new URL; `git fetch` to confirm it works.
4. Rename both local folders (`Move-Item`/`mv`). Keep the `prompts/` folders inside them intact — the
   board's queue and state move along with the folder.
5. Fix hardcoded references to the old names/URLs that are **terminal-owned**:
   - launcher `build-release.ps1` / `.gitignore` comments naming the dev repo
   - launcher `apply-skyrim-watcher.py` `Source:` URL (leave the upstream `Target:` URL alone)
   - both `TECHNICAL.md` files: repo links and folder paths
   - any other code/script with the old slug (grep both repos, excluding `.git`, `build`,
     `github-releases`, `prompts`)
   Commit each repo separately, by pathspec, after asking the director (ask-first preference), then push.
6. **Don't edit** `README.md`, `START HERE.txt`, `RELEASE_NOTES.md`, either `CLAUDE.md`, or the workspace
   root `README.md` / `TODO.md` — design side owns those and will update them from the handoff.

## Verify

- `gh repo view awesmdiver/skyrimnet-multiproxy` and `awesmdiver/skyrimnet-multiproxy-dev` both resolve;
  the public one is still public, the dev one still private.
- Both new folders exist, the old ones don't, `git status` is clean in each, `git remote -v` shows the
  new URLs.
- Launcher still configures/builds from its new folder (CMake cache may need a fresh `build\` dir —
  say what you did).

## Handoff

Write the handoff to the **new** launcher folder:
`F:\Claude Workspace\skyrim-modding\skyrimnet-multiproxy\prompts\handoff-latest.md`.
List every file outside this item's ownership that still says the old name (workspace README, both
project READMEs, START HERE, CLAUDE.md files, workspace TODO, memory notes) so the design side can
sweep them, and note whether the board picked up the renamed folders on its own.

The H1 must be exactly:
`# Handoff — Rename the GitHub repos and local folders to skyrimnet-multiproxy`
