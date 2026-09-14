# Technical documentation

Build instructions, architecture, release packaging, and the `proxy.py` patch internals for
SkyrimNet MultiProxy. See `README.md` for what it does in-game.

## Prerequisites

- CMake 3.24+
- MSVC Build Tools (VS 2022+) — the C++ compiler, full IDE not required
- Internet access on the first build only — CMake's `FetchContent` pulls CommonLibSSE-NG, fmt,
  spdlog, and rapidcsv automatically

## Building

Open an **x64 Native Tools Command Prompt for VS 2022**, then:

```cmd
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DCMAKE_SKIP_INSTALL_RULES=ON
cmake --build build --config Release
```

The first build takes several minutes (compiling CommonLibSSE-NG); later builds only recompile
changed files. The compiled DLL lands in `build/Release/SkyrimNetMultiProxy.dll`.

The build does **not** touch your live game install by default — test with a real
Vortex/MO2-style package install instead of a raw file copy. If you specifically want the old
convenience behavior (auto-copy the DLL into your game's `Data/SKSE/Plugins/` after every build),
opt in explicitly, supplying your own install path (never hardcoded/committed):

```cmd
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DCMAKE_SKIP_INSTALL_RULES=ON -DDEPLOY_TO_GAME=ON -DSKYRIM_PATH="C:/path/to/Skyrim Special Edition"
```

## Architecture

On `SKSEPlugin_Load` (before the main menu), the plugin:

1. Reads `Data\SKSE\Plugins\SkyrimNetMultiProxy.ini` — or, if that doesn't exist yet but the
   pre-rename `ProxyLauncher.ini` does (an upgrading install), copies that forward to the new
   filename and reads it instead, so an existing install's settings keep working without
   re-running setup. Logs when this fallback fires.
2. Checks whether the configured port is already listening — a manually pre-launched proxy (or a
   still-present old `ProxyLauncher.dll` racing to launch its own copy) is detected and left alone
   rather than duplicated.
3. If nothing's listening, launches `python proxy.py` as a detached, minimized console process via
   a pure Win32 process launcher (`proxy_launcher.cpp` has no CommonLibSSE dependency — it's a
   thin wrapper Skyrim's own event system happens to trigger).
4. Logs the result to `Documents\My Games\Skyrim Special Edition\SKSE\SkyrimNetMultiProxy.log`.
5. If `Data\SKSE\Plugins\ProxyLauncher.dll` (the pre-rename DLL) is still present, logs a one-time
   warning that it's dead weight and safe to delete, alongside `ProxyLauncher.ini`.

The proxy warms up in the background while the game loads; the first NPC conversation waits up to
60s for auth to be ready (handled inside the proxy itself, not this plugin).

## Release packaging

Starting with this release, the distributed zip bundles a complete, ready-to-run `proxy.py`
directly — no separate download, no patch step for the end user. That file is sourced from the
**private** `skyrimnet-multiproxy-dev` repo (a dev-only fork used to track/test changes against
upstream), copied in at release-build time rather than kept as a second tracked copy here — two
independently-editable copies of the same file drifting apart is exactly the bug that motivated
this change (see "What's changed from upstream" in `skyrimnet-multiproxy-dev`'s own TECHNICAL.md
for the history).

A release package contains:

```
SkyrimNetMultiProxy-vX.Y.Z.zip
├── SkyrimNetMultiProxy.dll   — the SKSE plugin
├── SkyrimNetMultiProxy.ini   — SKSE plugin config template
├── proxy.py                  — complete, ready-to-run proxy (from skyrimnet-multiproxy-dev)
├── proxy.ini.example         — proxy's own optional config template
├── config.example.json       — OpenRouter/GLM/Nano-GPT key template
├── requirements.txt          — proxy's Python dependencies
├── start-proxy.bat           — manual-launch helper (not required — the plugin starts it)
├── setup.ps1 / setup.bat     — checks Python, runs pip install, prints the remaining
│                                manual steps (tracked in this repo, not skyrimnet-multiproxy-dev —
│                                they're launcher-package-specific, reference
│                                SkyrimNetMultiProxy.dll/.ini by name)
├── START HERE.txt             — plain-language quick-start for the zip
└── LICENSE                   — MIT (proxy.py's original license, from upstream)
```

## `apply-skyrim-watcher.py` — maintenance tool, not part of normal use

`Claude-SkyrimNet-Proxy`'s `proxy.py` needs a few fixes/additions to work well launched this way.
**These are already applied to the `proxy.py` bundled in every release** — a normal user never
needs to run this script.

It exists so the exact same changes can be reproduced against a *different* or future copy of the
upstream file (a fresh `git clone` of `galanx/Claude-SkyrimNet-Proxy`, or a later upstream
version) — e.g. if upstream releases updates and `skyrimnet-multiproxy-dev` needs to be refreshed.
Validated 2026-07-26: running it against a completely fresh upstream clone reproduces the bundled
`proxy.py` (functionally identical; only differences are comments/import-order/one now-unused
parameter).

```cmd
python apply-skyrim-watcher.py path\to\proxy.py
python apply-skyrim-watcher.py path\to\proxy.py --enable   # also turns on auto-close immediately
```

- Takes a `.bak` backup before modifying anything.
- Safe to run more than once — detects if a patch is already applied.
- Safe to run against a version where the upstream PR has already merged — bug-fix hunks are
  skipped automatically rather than double-applied or conflicting.
- Creates `proxy.ini` alongside `proxy.py` if it doesn't exist yet.

### Bug fixes (two also submitted as [PR #5](https://github.com/galanx/Claude-SkyrimNet-Proxy/pull/5) upstream)

Applied unconditionally, and auto-skipped once upstream has merged/already has them:

- **`UnboundLocalError` on every streaming response.** `_usage` was only assigned in the
  non-streaming branch of `call_api_direct` but referenced unconditionally after the `if/else`.
  Since Claude always returns `text/event-stream`, the non-streaming branch never ran — every
  request through that path returned HTTP 500. *(In PR #5.)*
- **Token counts missing from `call_api_direct` log lines.** The streaming branch only parsed
  `content_block_delta` events and never collected usage data. Fixed by adding
  `message_start`/`message_delta` parsing (matching what `call_api_streaming_with_retry` already
  did), so log lines now show `| in=N out=N tok` consistently. *(In PR #5.)*
- **Auth-capture condition too strict.** Upstream requires both `"system"` and `"messages"` keys
  in the parsed request body before capturing auth — real requests with no system prompt never
  triggered a capture. Broadened to require only `"messages"`.
- **Auth header fallback.** The captured-auth log line now also checks `x-api-key`, not just
  `Authorization`, since some captures use that header instead.
- **OpenRouter key handling crash.** Upstream extracted the key per-request via an unguarded
  `request.headers.get("authorization").removeprefix(...)`, raising an unhandled `AttributeError`
  (an ugly 500) if the header was simply absent. Replaced with a module-level `openrouter_api_key`
  loaded once from `config.json`, plus a proper "not configured" error instead of a crash.
- **Default model bump.** `DEFAULT_MODEL` updated to a more current model — a preference, not a
  bug fix, and one that will go stale again; check `_hunk_default_model` if it needs updating.

### Skyrim-specific additions (`proxy.ini`)

```ini
[General]
; Shut the proxy down when Skyrim exits (polls every 10 s)
AutoCloseWithSkyrim = false

; Write proxy.log alongside proxy.py for debugging
EnableLogging = false

; Comma-separated Skyrim process names to watch
SkyrimProcess = SkyrimSE.exe, SkyrimVR.exe
```

Changes take effect the next time the proxy starts — no recompile or reinstall needed.

### Console title fix

When the proxy is launched by an external process (this plugin, or anything else), `claude
--print` runs at startup to capture auth headers and changes the console window's title as a side
effect. The patch restores the title to **Claude SkyrimNet Proxy** immediately after auth capture
completes -- this is the exact string `_hunk_console_title` inserts into a fresh upstream clone, so
it's left as-is here even after the 2026-09-13 SkyrimNet MultiProxy rename; the shipped plugin's
own console/error text uses the current name (see the sections above).

## Project structure

```
skyrimnet-multiproxy/
├── CMakeLists.txt          — build config; FetchContent handles all deps
├── apply-skyrim-watcher.py — maintenance-only patch script (see above)
├── SkyrimNetMultiProxy.ini — SKSE plugin config template (bundled in releases)
└── src/
    ├── PCH.h               — precompiled header (CommonLibSSE-NG)
    ├── main.cpp            — SKSE plugin entry point + logging
    ├── proxy_launcher.h    — launch result enum
    └── proxy_launcher.cpp  — pure Win32 process launcher (no CommonLibSSE deps)
```

## Dependencies (auto-downloaded by CMake)

- [CommonLibSSE-NG](https://github.com/CharmedBaryon/CommonLibSSE-NG) v3.7.0
- [fmt](https://github.com/fmtlib/fmt) 10.2.1
- [spdlog](https://github.com/gabime/spdlog) 1.13.0
- [rapidcsv](https://github.com/d99kris/rapidcsv) v8.83
