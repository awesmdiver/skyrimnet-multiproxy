# LaunchProxy test harness

A standalone way to exercise `LaunchProxy()` (`src/proxy_launcher.cpp`) — including the async
startup-timeout detection added 2026-09-20 — without needing a real running Skyrim, and without
touching the live install's `SkyrimNetMultiProxy.ini`/`.dll`.

This works because `proxy_launcher.cpp` is pure Win32 with zero CommonLibSSE dependency (see its
own top comment) — it compiles standalone with plain `cl.exe`, no CMake project needed.

## Why this exists

Built while diagnosing a real live incident: a broken `PythonExe` setting caused the spawned
process to exit instantly, but `SKSEPluginLoad` still logged "Proxy launched successfully" —
`LaunchProxy()` only checked whether `CreateProcessW` returned `TRUE`, never whether the process
actually did anything. The fix added an async background thread that polls the configured port
and reports if it never comes up. This harness is what verified that fix actually works, and it
found (and helped fix) a second, deeper bug in the process: `IsPortListening()`'s plain blocking
`connect()` took ~2 *real* seconds to fail on a refused loopback port on this dev machine (likely
a WSL2/Hyper-V virtual network adapter intercepting loopback traffic) — `SO_SNDTIMEO` never
actually bounded that. Worth re-running this harness any time `IsPortListening()` or
`WatchForStartupTimeout()` gets touched again.

## Build

```powershell
.\build.ps1
```

Produces `bin\instant_exit.exe`, `bin\test_launch.exe`, `bin\timing_check.exe`. Requires the same
Visual Studio C++ toolchain the real plugin builds with (edit `$vcvars` in `build.ps1` if yours
lives somewhere else).

## Run: the two `test_launch` scenarios

`LaunchProxy()` finds "its own" ini via `GetModuleFileNameW` — wherever the currently running exe
lives — so `test_launch.exe` needs a `Data\SKSE\Plugins\SkyrimNetMultiProxy.ini` sitting next to
it in `bin\`. `use-scenario.ps1` sets that up for you from the two templates in `ini-scenarios\`:

**Failure scenario** — `PythonExe` points at `instant_exit.exe` (simulates the real Store-stub
bug: a process that spawns successfully but does nothing and exits almost instantly):

```powershell
.\use-scenario.ps1 failure
.\bin\test_launch.exe
```

Expect: `LaunchProxy() returned Launched` almost immediately (a few hundred ms), then a
`[CALLBACK] port 18765 never came up after 20s` line printing after ~20 real seconds.

**Success scenario** — `PythonExe=py`, `ProxyScript` points at the real `proxy.py` (adjust the
path in `ini-scenarios\success.ini.template` first if your `skyrimnet-multiproxy-dev` checkout
isn't at the default path):

```powershell
.\use-scenario.ps1 success
.\bin\test_launch.exe
```

Expect: `LaunchProxy() returned Launched` almost immediately, and **no** `[CALLBACK]` line at all
within the 30s the harness waits — the real proxy actually started and bound port 8000, so the
watcher thread found it and returned quietly. This scenario spawns a **real, running** proxy
process — remember to `taskkill` the spawned `py.exe` afterward, same as any other manual launch.

This also exercises the `--plugin-version=X.Y.Z` argument `LaunchProxy()` appends to the launch
command (2026-09-20, so `proxy.py`'s dashboard can show which plugin build started it). `build.ps1`
defines `SKYRIMNET_MULTIPROXY_VERSION_STRING` as `"0.0.0-test"` for this standalone build (the real
value normally comes from CMakeLists.txt's `PROJECT_VERSION` -- see that file's own comment) — so
the dashboard's "Version" stat will correctly show `Proxy 3.0.0 · Plugin 0.0.0-test` and a version-
mismatch warning. **That mismatch warning is expected and correct here** — it proves the argument
made it all the way from the C++ command line through to the dashboard, not a bug in either repo.

## `timing_check.exe` — diagnostic, not pass/fail

Measures real wall-clock time for repeated `IsPortListening()` calls against a port nothing is
listening on:

```powershell
.\bin\timing_check.exe
.\bin\timing_check.exe 12345   # optional: a different port
```

Each call should take roughly ~500ms (the `select()` timeout `IsPortListening` uses) — if it's
consistently taking much longer (as it once did, ~2000ms, before that function's non-blocking
`connect()`+`select()` fix), something about this machine's network stack is slowing down refused
loopback connections again, and `WatchForStartupTimeout`'s real total wait time will end up much
longer than its documented ~20s (it tracks real elapsed time via `steady_clock` now, so it'll
still eventually fire correctly — just slower than intended, which this tool will make obvious).

## Files

| File | What it is |
|---|---|
| `test_launch.cpp` | The actual harness — calls the real `LaunchProxy()`, times it, waits to observe the async callback. |
| `instant_exit.cpp` | Trivial "exits immediately" program simulating the broken Python stub. |
| `timing_check.cpp` | Diagnostic-only: isolates `IsPortListening()`'s own real latency (has its own copy of the function, not linked against the real source — keep them in sync if `IsPortListening` changes). |
| `ini-scenarios\*.ini.template` | The two test configs, with a `{BINDIR}` placeholder `use-scenario.ps1` fills in. |
| `use-scenario.ps1` | Renders a template into `bin\Data\SKSE\Plugins\SkyrimNetMultiProxy.ini`. |
| `build.ps1` | Compiles everything into `bin\` (gitignored). |
