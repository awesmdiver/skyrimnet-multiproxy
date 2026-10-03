# LaunchProxy: detect a proxy that reports "launched" but never actually starts

Queued 2026-09-20, for the v3.0.0 release currently being prepared. Confirmed live during a real
troubleshooting session (see `prompts/handoff-latest.md` — "Incident 2"): a broken `PythonExe`
setting caused the spawned process to exit instantly (the Microsoft Store's Python stub — separately
fixed in `SkyrimNetMultiProxy.ini`, not this task), but `SKSEPluginLoad` still logged "Proxy launched
successfully," because `LaunchProxy()` only checks whether `CreateProcessW` returned `TRUE` — it never
checks whether the spawned process actually did anything. Any instant-death cause (bad `PythonExe`, a
bad path, a Python-level import error, anything) will always report false success with zero visible
error today. This task is the real, general fix for that class of bug — not the one-off python/py
fix, which already shipped separately.

## Current shape (read it yourself, this may have shifted)

`src/proxy_launcher.cpp` — `LaunchProxy()` (pure Win32, deliberately does **not** include
CommonLibSSE/PCH headers — see the file's own top comment and `proxy_launcher.h`'s docstring for why:
Winsock2 vs. `SKSE::WinAPI` header conflicts). It already has an `IsPortListening(int port)` helper
(used synchronously once already, to detect "already running" before launching). After a successful
`CreateProcessW`, it currently just closes the process/thread handles and returns
`ProxyLaunchResult::Launched` immediately — no verification the process is actually doing anything.

`src/main.cpp` — `SKSEPluginLoad` calls `LaunchProxy()` and logs the result via `SKSE::log::*`
(spdlog through CommonLibSSE — this file already includes it). The `Failed` case also pops an
interactive console via `ShowProxyError()`.

## What to build

After a successful launch, spawn a **detached background thread** that polls `IsPortListening(port)`
for a generous window — real startup takes ~4-5s even in the working case (the Claude auth-capture
subprocess call adds to it), so poll for something like 15-20 seconds before giving up — and reports
if the port never came up.

**Deliberately asynchronous, not synchronous inside `SKSEPluginLoad`** — blocking game startup for
15-20 seconds on every single launch, just to catch a rare misconfiguration, is a bad trade against
the common working case. This must not add any startup delay to the normal path.

**Deliberately a log message, not `ShowProxyError`'s interactive popup, for this async path** — by
the time this thread's window has elapsed, the player is likely already in-game; popping an
interactive console mid-gameplay would be a jarring interruption. (The existing synchronous `Failed`
case — a bad ini path, caught immediately, before the game has even loaded — is a different situation
and should keep its existing popup behavior unchanged.)

**The real architectural question to resolve**: `proxy_launcher.cpp` has no logging capability by
design (pure Win32, no CommonLibSSE). Don't break that separation by pulling `SKSE::log` (or any
CommonLibSSE header) into this file. Pick whichever of these reads cleanest given the existing code,
or propose your own — just keep the pure-Win32/has-logging split intact:

- `LaunchProxy` takes an optional plain C function-pointer callback (e.g.
  `void(*onPortNeverCameUp)(int port)`) that the async thread invokes if the window elapses with the
  port never listening; `main.cpp` passes a small function that calls `SKSE::log::error(...)`.
- Or: `LaunchProxy` returns enough information (the port, primarily) via an out-parameter for
  `main.cpp` to spawn the watcher thread itself, entirely on the CommonLibSSE side, reusing
  `IsPortListening` (already declared `static` in `proxy_launcher.cpp` — would need to be exposed, e.g.
  via `proxy_launcher.h`, if `main.cpp` is to call it directly).

Either is fine; use your own judgment for which fits the existing code shape better, but the log
message on the caller's side should read clearly, e.g. something like: `"Proxy process started but
never began listening on port <N> after <N>s -- it may have crashed immediately. Check proxy.log for
details."`

## Out of scope, don't add this pass

Checking whether the spawned process itself exited early (`WaitForSingleObject`/`GetExitCodeProcess`)
is a reasonable related idea, but wasn't part of the original recommendation and adds real scope
(process handle lifetime management currently closes both handles immediately after `CreateProcessW`
succeeds). Stick to the port-polling approach described above; note the process-exit idea as a
possible future enhancement in the handoff if you want, don't build it this pass.

## Build & verification

- This is a real `src/` change — rebuild the DLL (CMake/Visual Studio, Release config) before
  anything else touches the release. `skse-project.json`'s `Version` is already at `3.0.0` for this
  release; no version bump needed, but the binary DOES need rebuilding now that `src/` has actually
  changed (unlike the rest of this release, which shipped the v2.5.0 binary unchanged).
- Manually verify the working path still launches normally with no added delay (the async thread
  should be invisible when everything works).
- If practical, verify the failure path: point `PythonExe` at something that starts and exits
  instantly (e.g. a bare `cmd.exe /c exit`) and confirm the log message appears after the polling
  window, with the game/plugin otherwise unaffected.
- Update `RELEASE_NOTES.md`'s v3.0.0 section (`## 🔧 Improvements & Polish`) to describe this in
  plain, non-technical language for players — something like: "If the proxy fails to start for any
  reason, you'll now see a clear message about it in the log instead of a false 'launched
  successfully' — this was already fixed for one specific cause (the Python Store-stub issue above),
  and now works as a general safety net for any other cause too."

When verification passes, commit + push (narrow `git add`, only the files you actually changed).

**Handoff:** title it exactly `# Handoff — LaunchProxy: detect a proxy that reports "launched" but never actually starts`.
