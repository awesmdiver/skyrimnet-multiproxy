# launch-outside-job-test

Proves the two risky parts of the mod's start-up without running Skyrim. Everything links the REAL
`../../src/multiproxy_channel.cpp`, so it always tests the source the mod ships.

Build: `pwsh build.ps1` (plain `cl.exe` via vcvars; edit `$vcvars` if Visual Studio lives elsewhere).

## 1. Does the mod manager unlock after the game closes?

`job_box.exe <mode> <probe.exe> ["probe arguments"]` runs the probe inside a job object, like Mod
Organizer 2 runs Skyrim, waits for the probe to exit ("the game closed"), then waits up to 10 s for the
job to empty ("the manager unlocks") and pings MultiProxy to see it is still up.

| mode | job | expected |
| :--- | :--- | :--- |
| `breakaway` | allows `CREATE_BREAKAWAY_FROM_JOB` | tray started outside the job, UNLOCKS, MultiProxy still up |
| `silent` | `SILENT_BREAKAWAY_OK` | same |
| `none` | no breakaway at all | `CreateProcess` fails with error 5, Explorer starts it, UNLOCKS |
| `nojob` | no job (what Vortex does) | nothing to wait on |

**Negative control** (the harness must be able to fail): leave a child inside the job and it must say
`STAYS LOCKED`:
`job_box.exe breakaway C:\Windows\System32\cmd.exe "/c start /b ping -n 20 127.0.0.1 >nul"`

`job_probe.exe` is the stand-in game: it runs the mod's real `RunStartup` against whatever the pointer
file names. It needs a real MultiProxy install file; for a scratch run, point `MULTIPROXY_INSTALL_INFO`
at one, **but** note a tray app started through Explorer (`none` mode) does not inherit your
environment and writes the pointer file at its default `%APPDATA%` path, so a scratch run needs that
real default location (and a spare MultiProxy port, and a free tray lock port 18777).

## 2. Does "Skyrim closed" reach MultiProxy at exit?

`exit_probe.exe <ExitProcess|return|TerminateProcess> [preload]` loads `exit_probe_dll.dll` (the mod's
channel code with its real `DllMain`), sends "started", ends the process, and you read
`GET /local/tray-status` for `closedSeq`. Expected: goes up for `ExitProcess` and `return`, does NOT for
`TerminateProcess` (nothing runs; documented limit). `preload` starts Winsock before the DLL loads, which
is what the plugin's static `mswsock.dll` import emulates; see `TECHNICAL.md`, "The exit notice".
