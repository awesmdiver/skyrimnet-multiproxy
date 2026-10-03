# Handoff — Plugin: start MultiProxy outside the game, and tell it when Skyrim starts and closes

**Status:** built, builds clean (`/W4 /WX`), checked against the REAL tray app. Version 3.2.0 in `CMakeLists.txt`, `skse-project.json`, and (dev repo, commit `d8d8317`) `PROXY_VERSION`. Nothing published. `README.md` untouched.

## What the mod does now
- **Pointer file present** (`%APPDATA%\SkyrimNet MultiProxy\install-info.json`, or `MULTIPROXY_INSTALL_INFO`): on a background thread it pings; if nothing answers it starts the tray app hidden **outside the mod manager's job** (`CREATE_BREAKAWAY_FROM_JOB` → if the job forbids it, Explorer starts it → last resort inside the job, with a warning), waits up to 60 s for the ping, then POSTs "Skyrim started" (`folder`, `modVersion`, plus an extra `pid`). At exit it POSTs "Skyrim closed". It never kills anything.
- **No usable pointer file:** the old way still runs if `SkyrimNetMultiProxy.ini` names a program that exists (an ini that says `proxy.py` still finds `multiproxy.py` and the other way round). Otherwise one log line, nothing in game. An unreadable pointer file logs a warning, then the same fallback.
- New package `github-releases\SkyrimNetMultiProxy-Mod-3.2.0.zip` (only `SKSE\Plugins\SkyrimNetMultiProxy.dll`, asserted by the build). Deliberately no `.ini`.
- Renames done: sample ini, `build-release.ps1`, `sync-release-staging.ps1`, `RELEASING.md`, `TECHNICAL.md`, `START HERE.txt`, `setup.ps1`; `release-staging\` re-synced (old `proxy.py` / `proxy.ini.example` removed from it).

## Proof (real tray app, scratch copy, spare ports; nothing touched the live proxy on 8000)
Test tools: `tools/launch-outside-job-test/` (README inside). `job_box.exe` runs the mod's real start-up inside a job object and waits for the job to empty, like Mod Organizer 2:
- job allows breakaway / breaks away silently → tray outside the job, job empties at once ("UNLOCKS"), MultiProxy still up after.
- job forbids breakaway → `CreateProcess` fails (error 5), **Explorer fallback starts it**, job empties, MultiProxy still up.
- no job (what Vortex does) → fine.
- negative control (child left inside the job) → "STAYS LOCKED", so the check can fail.
- "started" made the server show the Skyrim folder and mod version; "closed" raised `closedSeq`.

**Found and fixed while testing:** "closed" never arrived at a normal exit. Windows unloads DLLs in reverse load order and Winsock's own provider DLL loads after the mod, so it was already gone when the mod's exit code ran. Fix: the mod imports `mswsock.dll` statically (verified in the built DLL with `dumpbin`; the linker drops it unless forced). Re-checking this after linker changes is in `RELEASING.md` and `TECHNICAL.md`.

## NOT proven — what the director needs to do
1. **Real Mod Organizer 2 and real Vortex were not available here** (neither is installed). The job-object harness mimics MO2's job; it is not MO2. To check for real: with MultiProxy installed (tray app has run once so the pointer file exists), close the tray app, launch Skyrim through MO2, quit the game, and confirm (a) MO2's window unlocks right away, (b) the MultiProxy tray icon is still there if the tray was started by the mod and "Start and stop with Skyrim" is off. Then same through Vortex. `Documents\My Games\Skyrim Special Edition\SKSE\SkyrimNetMultiProxy.log` says which start path was used ("started outside the job" / "through Explorer" / the warning).
2. **A real Skyrim exit was not run.** If Skyrim ends with `TerminateProcess` (crash, End Task, some quit paths) no code in the game process can run, so no "closed" is sent; the test confirms exactly that. **Recommended follow-up (dev repo / tray):** have the tray app watch the `pid` the mod now sends in "started" and treat its exit as "closed". A few lines, and it also covers crashes.

## Judgment calls
- Last-resort fallback starts the tray **inside** the job rather than not at all (MultiProxy running beats no AI). Risk: MO2 may stay locked until the tray quits.
- Pointer file honours `MULTIPROXY_INSTALL_INFO` (same override the MultiProxy side has); extra `pid` field in "started".
- If the server isn't answering but a tray app is already running (player stopped the server from the tray), the mod still launches the tray command; per the tray's own rule that just opens the dashboard.
- I bumped `PROXY_VERSION` in the dev repo myself (one line, own commit): a mismatch makes players see the "versions don't match" warning now that the mod reports its version.

## Things seen, not mine to fix
- **`release-staging` list is stale:** `multiproxy.py` imports dozens of program files (courier, routing, voice, local models, tray…) that neither `sync-release-staging.ps1` nor `RELEASING.md` lists, so the existing zip's program would not start. Belongs to the Windows installer item.
- `build-release.ps1` builds in `$env:TEMP` (workspace rule says `F:\Claude Temp Files`).
- Design-side files still say "plugin" to players: `README.md` (15 places), `RELEASE_NOTES.md` (8).
- Linux/Proton is not special-cased in the mod.

## Player-facing strings I wrote or changed (for the Gemini pass)
Where: `src/main.cpp` (game log + the legacy error console), `START HERE.txt`. "plugin" → "mod" in each.
- Error console, legacy way (`ShowProxyError`): "Failed to start MultiProxy." / "Check ProxyScript and WorkDir in Data\SKSE\Plugins\SkyrimNetMultiProxy.ini" (was "Failed to start the proxy.")
- Log: "MultiProxy is installed (port {}); starting it outside the game if needed"
- Log: "The MultiProxy install file exists but could not be used ({}). Run the MultiProxy installer again."
- Log: "MultiProxy is not installed on this PC (no install file, and SkyrimNetMultiProxy.ini names no program); nothing to start"
- Log: "MultiProxy launched (old way, from SkyrimNetMultiProxy.ini)" / "MultiProxy already running on the configured port — skipping launch" / "Failed to launch MultiProxy — check SkyrimNetMultiProxy.ini paths"
- Log: "MultiProxy started but never began listening on port {} after {}s -- it may have crashed immediately. Check multiproxy.log (or proxy.log in an old setup) for details."
- Log (legacy DLL): "…this mod has been renamed to SkyrimNetMultiProxy.dll. You can delete the old ProxyLauncher.dll and ProxyLauncher.ini whenever you're ready; they're no longer used."
- Channel log (`src/multiproxy_channel.cpp`): "MultiProxy is already running", "MultiProxy is not answering; starting the tray app", "Tray app started outside the job", "Tray app started through Explorer", "MultiProxy answered", "Told MultiProxy that Skyrim started", "MultiProxy did not answer within 60 seconds of starting the tray app. Look at multiproxy-tray.log in the MultiProxy settings folder.", "MultiProxy answered a ping but would not take 'Skyrim started' (a changed token? run the MultiProxy installer again)", "Could not start it outside the job; it is running inside it. Mod Organizer 2 may stay locked after Skyrim closes until the tray app quits."
- `START HERE.txt`: "The new mod will copy your old ProxyLauncher.ini settings…" and the `multiproxy.py` / `multiproxy.ini.example` names.
