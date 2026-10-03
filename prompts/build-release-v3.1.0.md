# Build the v3.1.0 release zip

Follow `RELEASING.md` steps 1–5 exactly. **Do not tag, publish, or create a GitHub release.** The
director approves that separately.

## What's in it

Proxy changes since v3.0.0, all committed and pushed in `skyrimnet-multiproxy-dev` (`main`): Raydonn's
upgrades, SkyrimNet Beta 25 readiness, the MO2 folder fix, DeepSeek reasoning off by default, and the
Speeds-tab privacy fix, and Speeds v2. Confirm with `git log` in the dev repo that the commit for **"Stop flipping
SkyrimNet's usage-sharing setting from the Speeds tab"** and **"Speeds v2: health at a glance, "starts talking" time, and reliability labels"** are both there (plus the two follow-ups: Speeds v2 fixes and the job-description swap) before staging. If it isn't, stop and
say so in the handoff. `src/` (the plugin) has no code changes this release.

## Steps

1. **Bump the version to 3.1.0 in BOTH places:** `skse-project.json` → `Version`, and `CMakeLists.txt`
   → `project(SkyrimNetMultiProxy VERSION 3.1.0 ...)`. They must agree.
2. **Reconfigure and rebuild the plugin** (a real `cmake -B build -S ...` reconfigure, not just
   `cmake --build`), then confirm the rebuilt `.dll` really reports **3.1.0** (the
   `tools/launch-proxy-test` harness or `SkyrimNetMultiProxy.log`). Don't trust the files alone.
3. **Stage** with `sync-release-staging.ps1` (the six-file manifest only).
4. **Build** with `.\build-release.ps1` → `github-releases\SkyrimNetMultiProxy-v3.1.0.zip`.
5. **Check the zip:** list its contents and confirm there's no `config.json`, `proxy.ini`, `.log`,
   `tests`, or `prompts`; there's one top-level `SkyrimNet MultiProxy\` folder; and `LICENSE-proxy.txt`
   is present. Confirm the staged `proxy.py` matches the dev repo's current `main` byte for byte.
6. Commit the version bump (`git commit -- skse-project.json CMakeLists.txt`) and push. Don't commit
   anything under `prompts/` or `release-staging/`.

## Handoff

`prompts/handoff-latest.md`, titled exactly `# Handoff — Build the v3.1.0 release zip`. Include the zip
path and size, the full zip listing, the reported `.dll` version, and the dev-repo commit the staged
`proxy.py` came from.
