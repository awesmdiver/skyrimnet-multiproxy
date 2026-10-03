# Technical documentation

Build instructions, architecture, release packaging, and the `multiproxy.py` patch internals for
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

## Versioning

`CMakeLists.txt`'s `project(... VERSION X.Y.Z ...)` is the one place a version bump happens.
Everything else reads from it — there is no second literal to remember:

- `SKYRIMNET_MULTIPROXY_VERSION_STRING` (`main.cpp`'s startup log line) is `PROJECT_VERSION` as a
  string.
- `SKSEPluginInfo`'s `.Version` field (what SKSE, crash logs, and mod managers report for the
  plugin itself) is built from `PROJECT_VERSION_MAJOR`/`_MINOR`/`_PATCH`, the same components
  `project()` derives from that one `VERSION X.Y.Z`.

Both used to be separate hardcoded literals. The log string was fixed to track `PROJECT_VERSION` in
the v2.3.0 bump; `.Version` was missed and stayed at `2.0.0.0` through v2.1.0-v2.3.0 despite three
real version bumps, silently telling anyone reading a crash log or a mod manager's plugin list the
wrong version. Fixed in the v2.4.0 cycle by wiring `.Version` to the same `PROJECT_VERSION`
components rather than hand-writing a fourth copy of the number — bump `CMakeLists.txt` once and
both move together, confirmed by temporarily building against a fake version and reading the
result back out of the compiled DLL (`SKSEPlugin_Query`'s reported `version`, not just the log
string) rather than trusting the build succeeded.

## Architecture

On `SKSEPlugin_Load` (before the main menu) the SKSE plugin picks one of two ways to get MultiProxy
running. Everything slow happens on a background thread, so Skyrim's loading is never held up.

**The new way — the installer's pointer file exists** (`src/multiproxy_channel.cpp`, pure Win32):

1. Reads `%APPDATA%\SkyrimNet MultiProxy\install-info.json` (`MULTIPROXY_INSTALL_INFO` overrides the
   path, same as on the MultiProxy side). The contract — fields, token, the three local routes — is in
   the dev repo's `TECHNICAL.md`, "The contract for the Skyrim mod". The plugin reads `port`, `token`,
   `tray.command`, `tray.workingDirectory` and `tray.fromSkyrimArg` with a small built-in JSON reader.
2. `GET /local/ping`. If MultiProxy answers, nothing is started.
3. If not, starts `tray.command` + `--from-skyrim` **hidden and outside the mod manager's job**, then
   pings until it answers (up to 60 s: the server loads models and checks its AIs). Order:
   `CreateProcess` with `CREATE_BREAKAWAY_FROM_JOB` → if the job forbids breakaway (error 5), ask
   Explorer to start it (`IShellDispatch2::ShellExecute` via the desktop shell, so the process is
   Explorer's child, in no job of ours) → last resort, start it inside the job and log a warning.
   Why: Mod Organizer 2 runs the game inside a job object and waits for the job to empty before it
   unlocks its window; a tray app left inside would keep MO2 locked after Skyrim closes.
4. `POST /local/skyrim/started` with `{"folder", "modVersion", "pid"}` (`pid` is extra, for a tray-side
   double check; the server ignores fields it doesn't know).
5. At game exit, `POST /local/skyrim/closed`. **The plugin never kills a process**: whether MultiProxy
   quits is the tray app's call ("Start and stop with Skyrim", and only if the mod started the tray).

**The old way — no usable pointer file.** If `SkyrimNetMultiProxy.ini` (or the pre-rename
`ProxyLauncher.ini`, copied forward to the new name) names a program that exists, the plugin launches
it as before (`proxy_launcher.cpp`: `py multiproxy.py --plugin-version=…` in a minimized console; an
ini that says `proxy.py` still works, the launcher tries the other name when the file is missing).
Otherwise (a mod-manager install where the installer never ran, the ini still holding the sample
path) it logs one line and stays quiet. An unreadable pointer file logs a warning and falls to the
old way.

Linux/Proton: the plugin runs inside Wine and cannot start a native Linux program, and this version
does not special-case it; the pointer file is looked for inside the Wine prefix and won't be there.

### The exit notice, and why it needed a linker trick

Skyrim gives SKSE no "closing" message, so `closed` is sent from `DllMain(DLL_PROCESS_DETACH)` when
`lpReserved != nullptr` (the process is ending). That runs under the loader lock with every other
thread gone, so it does one loopback request over Winsock that is already started. Three facts,
all measured (`tools/launch-outside-job-test`, `exit_probe`):

- **Windows detaches DLLs in reverse load order.** Winsock's base provider (`mswsock.dll`) is loaded
  by `ws2_32` on the first socket, which is *after* this DLL, so it is already torn down when this
  DLL's `DllMain` runs and the request silently fails. Fix: import `mswsock.dll` statically
  (`#pragma comment(lib…)` plus `/INCLUDE:__imp_TransmitFile`, because IPO and `/OPT:REF` otherwise drop
  an import nothing really calls) so the loader maps it *before* this DLL. Verify with
  `dumpbin /imports SkyrimNetMultiProxy.dll | findstr /i mswsock` after any linker change.
- **`TerminateProcess` runs nothing.** A crash, End Task, or a game exit that ends in
  `TerminateProcess` sends no notice. The tray app has the game's pid from `started` and can watch it
  as a backstop (not built yet; see the handoff).
- It does not run when the DLL is unloaded on its own (`lpReserved == nullptr`).

The proxy warms up in the background while the game loads; the first NPC conversation waits up to
60s for auth to be ready (handled inside the proxy itself, not this plugin).

### Test tools

`tools/launch-outside-job-test/` (`build.ps1`, then see its README) links the real
`multiproxy_channel.cpp`: `job_box.exe` runs a probe inside a job object like a mod manager (four job
modes plus a negative control that must report "STAYS LOCKED"), `exit_probe.exe` checks the exit
notice. `tools/launch-proxy-test/` still covers the old launcher.

## Release packaging

Starting with this release, the distributed zip bundles a complete, ready-to-run `multiproxy.py`
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
├── multiproxy.py             — complete, ready-to-run proxy (from skyrimnet-multiproxy-dev)
├── multiproxy.ini.example    — proxy's own optional config template
├── config.example.json       — OpenRouter/GLM/Nano-GPT key template
├── requirements.txt          — proxy's Python dependencies
├── start-proxy.bat           — manual-launch helper (not required — the plugin starts it)
├── setup.ps1 / setup.bat     — checks Python, runs pip install, prints the remaining
│                                manual steps (tracked in this repo, not skyrimnet-multiproxy-dev —
│                                they're launcher-package-specific, reference
│                                SkyrimNetMultiProxy.dll/.ini by name)
├── START HERE.txt             — plain-language quick-start for the zip
└── LICENSE                   — MIT (multiproxy.py's original license, from upstream)
```

`build-release.ps1` also writes `github-releases\SkyrimNetMultiProxy-Mod-X.Y.Z.zip`: just the mod,
laid out for mod managers (`SKSE\Plugins\SkyrimNetMultiProxy.dll`, nothing else; asserted on every
build). No `.ini` on purpose: with the installer the mod finds MultiProxy through the pointer file and
needs none, and a mod-manager update must not overwrite an old setup's edited one. The Windows
installer item picks this file up. It is built before the staging checks, so it never depends on
`release-staging\`.

## Staging and building a release — the dev/public boundary

`RELEASING.md` is the authority on the full release process and the exact manifest; this section
covers the two scripts that enforce it.

`release-staging\` (gitignored) is the one place a file from the private `skyrimnet-multiproxy-dev`
repo can leak into a published zip — `config.json` (live API keys) sits one character away from
`config.example.json` (which does ship) in that repo's root, so a folder copy or a glob is a real
credential-leak risk, not a hypothetical one. Both scripts below treat that as the thing to guard
against, not just to document.

**`sync-release-staging.ps1`** replaces the hand-copy step. It resolves `skyrimnet-multiproxy-dev`
as a sibling of this repo (`..\skyrimnet-multiproxy-dev`, via `$PSScriptRoot` — no hardcoded
absolute path), then copies exactly the six files RELEASING.md's manifest names, by explicit
source→destination pairs (`LICENSE` → `LICENSE-proxy.txt` renamed on the way in). Never a folder
copy, never a wildcard — a new file added to the dev repo's root does not get staged until someone
deliberately adds it to the manifest in both this script and RELEASING.md. Before copying anything,
it checks `release-staging\` itself against RELEASING.md's "What must never cross" list and refuses
(naming the offender) if anything on it is already sitting there — it does not delete the file
itself, since a stray `config.json` means someone copied the wrong thing and needs to know, not have
it silently cleaned up. Each staged file is hash-compared against what was already there so the
output reports changed vs. unchanged per file.

**`build-release.ps1`** is the safety net, not the only one — it re-checks independently rather than
trusting the sync script ran, in case `release-staging\` was populated by hand (still supported; see
its own header comment). Before assembling the zip it: (1) re-runs the same never-publish check
against `release-staging\`, (2) parses `config.example.json` and flags any non-empty value that
doesn't match an "obviously a placeholder" shape (empty string, `YOUR_...`, `CHANGE_ME`, `<...>`,
etc.) — shape-based rather than matching one vendor's key format, so it still catches a real key
staged under the example's name even for a provider added after this check was written, and (3)
re-checks the fully assembled staging tree right before `Compress-Archive` runs, since that's the
literal input to the zip. After the zip exists, it opens it back up and asserts the RELEASING.md
step 5 positives — exactly one top-level `SkyrimNet MultiProxy\` folder, `LICENSE-proxy.txt`
present, no `config.json`/`proxy.ini`/`.log`/`tests`/`prompts` anywhere inside — before printing
success, so a broken assumption fails the build instead of shipping quietly.

The never-publish name/dir list is duplicated between the two scripts rather than factored into a
shared module (matching this repo's existing convention of small, independent scripts); RELEASING.md
is the authority both were written against, so if that list changes there, both scripts need the
matching edit.

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
    ├── multiproxy_channel.h/.cpp — the local channel to MultiProxy (pointer file, ping, start outside the job, started/closed)
    ├── proxy_launcher.h    — launch result enum
    └── proxy_launcher.cpp  — pure Win32 process launcher (no CommonLibSSE deps)
```

## Dependencies (auto-downloaded by CMake)

- [CommonLibSSE-NG](https://github.com/CharmedBaryon/CommonLibSSE-NG) v3.7.0
- [fmt](https://github.com/fmtlib/fmt) 10.2.1
- [spdlog](https://github.com/gabime/spdlog) 1.13.0
- [rapidcsv](https://github.com/d99kris/rapidcsv) v8.83
