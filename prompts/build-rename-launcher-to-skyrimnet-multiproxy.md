# Rename the launcher and its plugin to SkyrimNet MultiProxy

The proxy now supports eight AI providers, not just Claude, so the director renamed the project
(decided 2026-09-13):

| Thing | Old | New |
|---|---|---|
| Display / UI name | Claude SkyrimNet Proxy (Launcher) | `SkyrimNet MultiProxy` (no hyphen) |
| SKSE plugin DLL | `ProxyLauncher.dll` | `SkyrimNetMultiProxy.dll` |
| Plugin config | `ProxyLauncher.ini` | `SkyrimNetMultiProxy.ini` |
| Plugin log | `ProxyLauncher.log` | `SkyrimNetMultiProxy.log` |
| Importable plugin archive | `ProxyLauncher.zip` | `SkyrimNetMultiProxy.zip` |
| Release zip | `ClaudeSkyrimNetProxyLauncher-vX.Y.Z.zip` | `SkyrimNetMultiProxy-vX.Y.Z.zip` |
| This public repo (renamed later, separate item) | `claude-skyrimnet-proxy-launcher` | `skyrimnet-multiproxy` |

The director explicitly chose to rename the DLL/ini too, knowing it affects existing installs — so the
upgrade path below is part of the job, not optional.

This item is **only the in-code / in-build name inside this repo**. Don't rename the folder, the GitHub
repo, or the git remote — a later item does that from the workspace root. **Don't cut a release** or
bump the version; flag in the handoff that a rename like this reads as a major version (v3.0.0).

**Before touching the C++:** read the Skyrim toolkit guide at
`E:\SteamLibrary\steamapps\common\Skyrim Special Edition\CLAUDE.md` (CommonLibSSE-NG / SKSE plugin
rules).

## Change

- **Plugin identity:** `CMakeLists.txt` target/project name, `skse-project.json` (`Name`, and
  `Description` → `Launches SkyrimNet MultiProxy on game startup`), `src/PCH.h` `PLUGIN_NAME`,
  `src/main.cpp` plugin `.Name`, header comments, the version log line, and every user-visible string
  in the error console window.
- **Config / log paths:** `src/proxy_launcher.cpp` ini path → `Data\SKSE\Plugins\SkyrimNetMultiProxy.ini`;
  log file → `SkyrimNetMultiProxy.log`; the error text pointing at the ini/log.
- **Rename the repo's `ProxyLauncher.ini` → `SkyrimNetMultiProxy.ini`** (`git mv`).
- **`setup.ps1`** and **`build-release.ps1`**: all names per the table (archive names, DLL/ini paths,
  on-screen text, comments). `build-release.ps1`'s `build\Release\...dll` path must match the new CMake
  output name.
- **`.gitignore`**: update comments that name the old repos/files.
- **Don't hand-edit `release-staging/proxy.py` or `release-staging/start-proxy.bat`** — they're copied in
  from the dev repo at release time (the dev repo gets its own rename item).
- **`apply-skyrim-watcher.py`**: it patches the *upstream* galanx/Claude-SkyrimNet-Proxy, so the strings
  it **matches against** in upstream code (e.g. `"Claude SkyrimNet Proxy"` inside a patch hunk) must stay
  exactly as upstream has them. Only update this project's own self-references (e.g. its `Source:` URL
  line can wait for the repo rename item — leave URLs alone here). Explain what you changed and didn't.
- **Don't edit `README.md`, `START HERE.txt`, or `RELEASE_NOTES.md`** — design side owns user-facing
  docs. List every spot in them that needs the new name in the handoff.

## Upgrade path for existing users (required)

Someone updating will still have `Data\SKSE\Plugins\ProxyLauncher.dll` + `ProxyLauncher.ini` from the
old version. Handle both:

1. **Settings carry over.** If `SkyrimNetMultiProxy.ini` doesn't exist but `ProxyLauncher.ini` does,
   read the settings from the old ini (log that it did) so the user's paths keep working without
   re-setup. Pick whether to also copy it to the new name, and justify it in the handoff.
2. **Old plugin left behind.** If `ProxyLauncher.dll` is still in `Data\SKSE\Plugins`, log a warning
   and tell the user once, plainly, that the old file can be deleted. The existing "proxy already
   running on this port" check should already stop a double launch — confirm that by reading the code,
   and say so in the handoff.

## Verify

- Clean configure + Release build with the real toolchain; the output is `SkyrimNetMultiProxy.dll`.
- `build-release.ps1` runs end to end into a scratch location under
  `F:\Claude Temp Files\claude-skyrimnet-proxy-launcher\` (not `github-releases\`) and produces the
  new archive names with the right internal layout (`SKSE\Plugins\SkyrimNetMultiProxy.dll` + `.ini`).
- `grep -rni "ProxyLauncher\|claude.skyrimnet"` (excluding `.git`, `build`, `github-releases`,
  `prompts`) — every remaining hit is intentional (upstream match strings, the old-file upgrade
  checks, design-owned docs) and listed in the handoff.
- In-game testing isn't expected from this item; say plainly what was and wasn't tested.

## Commit + handoff

Ask the director before committing, then commit only the files you changed, by pathspec (`git mv`
renames included).

In the handoff, list every new or changed user-facing string verbatim (old → new) with its file and
spot — especially the new "old plugin can be deleted" message, which goes to a Gemini copy pass.

End by writing `prompts/handoff-latest.md` with the H1 titled exactly:
`# Handoff — Rename the launcher and its plugin to SkyrimNet MultiProxy`
