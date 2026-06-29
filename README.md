# ProxyLauncher

([galanx/Claude-SkyrimNet-Proxy](https://github.com/galanx/Claude-SkyrimNet-Proxy)) is a great way to leverage your Claude subscription with ([MinLL/SkyrimNet-GamePlugin](https://github.com/MinLL/SkyrimNet-GamePlugin)). However, for me I always forgot start or stop the proxy so I decided to create an SKSE plugin that automatically launches the **Claude SkyrimNet proxy** (`proxy.py`) when Skyrim starts via `skse64_loader.exe` — so you don't have to run it manually before every session or exit it manually.

## How it works

On `SKSEPlugin_Load` (very early, before the main menu), the plugin:

1. Checks whether port 8000 is already listening (so a manually pre-launched proxy is respected)
2. If not running, launches `python proxy.py` as a detached, minimised console process
3. Logs the result to `Documents\My Games\Skyrim Special Edition\SKSE\ProxyLauncher.log`

The proxy warms up in the background while the game loads. The first NPC conversation will wait up to 60 s for auth to be ready (handled inside the proxy itself).

**Log files** (both in `Documents\My Games\Skyrim Special Edition\SKSE\`):

| File | Created by | Contains |
|------|-----------|----------|
| `ProxyLauncher.log` | SKSE plugin (always) | Launch status |
| `proxy.log` | proxy.py (`EnableLogging = true`) | Full proxy activity — alongside `proxy.py` |

## Configuration

Edit `Data/SKSE/Plugins/ProxyLauncher.ini` — no recompile needed:

```ini
[General]
; Path to the Python executable. "python" works if it's on your PATH.
PythonExe=python

; Full path to proxy.py — set this before launching Skyrim.
; Example: C:\Users\YourName\.local\bin\proxy.py
ProxyScript=

; Working directory for the proxy process (folder containing proxy.py and config.json).
; Example: C:\Users\YourName\.local\bin
WorkDir=

; Port the proxy listens on — used to detect if it's already running
Port=8000
```

## Installation

**Via mod manager (recommended):** install `ProxyLauncher-v1.1.0.zip` through MO2 or Vortex — the files land in `Data/SKSE/Plugins/` automatically.

**Manually:**
1. Copy `ProxyLauncher.dll` → `Data/SKSE/Plugins/`
2. Copy `ProxyLauncher.ini` → `Data/SKSE/Plugins/` and edit paths as needed
3. Launch Skyrim via SKSE (Vortex, MO2, or `skse64_loader.exe` directly)

## Patching proxy.py

`apply-skyrim-watcher.py` patches the upstream proxy ([galanx/Claude-SkyrimNet-Proxy](https://github.com/galanx/Claude-SkyrimNet-Proxy)) with several improvements. Some are bug fixes submitted upstream as a PR; others are Skyrim-specific features controlled via `proxy.ini`.

**Apply the patch:**

```cmd
python apply-skyrim-watcher.py path\to\proxy.py
```

Add `--enable` to turn on auto-close immediately:

```cmd
python apply-skyrim-watcher.py path\to\proxy.py --enable
```

The script:
- Creates a `.bak` backup before modifying anything
- Is safe to run more than once (detects if already applied)
- Is safe to run against a version where the upstream PR has already been merged (bug-fix hunks are skipped automatically)
- Creates `proxy.ini` alongside `proxy.py` if it doesn't exist

### Bug fixes (also submitted as [PR #5](https://github.com/galanx/Claude-SkyrimNet-Proxy/pull/5) upstream)

These are applied unconditionally and skipped automatically if the upstream has already merged them.

**`UnboundLocalError` crash on every streaming response**

`_usage` was only assigned in the non-streaming branch of `call_api_direct` but referenced unconditionally after the `if/else`. Since Claude always returns `text/event-stream`, the non-streaming branch never ran, causing every request through that path to return HTTP 500:

```
UnboundLocalError: cannot access local variable '_usage' where it is not associated with a value
```

**Token counts missing from `call_api_direct` log lines**

The streaming branch in `call_api_direct` only parsed `content_block_delta` events and never collected usage data, so log lines never showed token counts. The fix adds `message_start` / `message_delta` parsing (matching what `call_api_streaming_with_retry` already did). Log lines now show `| in=N out=N tok` for all responses.

### Skyrim-specific features (controlled via `proxy.ini`)

| Option | Default | Description |
|--------|---------|-------------|
| `AutoCloseWithSkyrim` | `false` | Monitor the Skyrim process and shut the proxy down when the game exits |
| `EnableLogging` | `false` | Write `proxy.log` alongside `proxy.py` — fresh file each session, API keys redacted |

### Console title fix

When the proxy is launched by the SKSE plugin (or any external process), `claude --print` runs at startup to capture auth headers and changes the console window title as a side effect. The patch restores the title to **Claude SkyrimNet Proxy** immediately after the auth capture completes.

### `proxy.ini` reference

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

## Build Requirements

| Tool | Notes |
|------|-------|
| CMake 3.24+ | [cmake.org](https://cmake.org/download/) |
| MSVC Build Tools (VS 2022+) | C++ compiler — full IDE not needed |
| Internet (first build only) | CMake downloads CommonLibSSE-NG, fmt, spdlog, rapidcsv automatically |

## Building

Open an **x64 Native Tools Command Prompt for VS 2022**, then:

```cmd
cmake -B build -S . -DCMAKE_BUILD_TYPE=Release -DCMAKE_SKIP_INSTALL_RULES=ON
cmake --build build --config Release
```

The first build takes several minutes (compiling CommonLibSSE-NG). Subsequent builds are fast — only changed files recompile.

The post-build step automatically copies the DLL to `Data/SKSE/Plugins/` if `SKYRIM_PATH` in `CMakeLists.txt` points to your installation.

## Project Structure

```
ProxyLauncher/
├── CMakeLists.txt          # Build config — FetchContent handles all deps
├── apply-skyrim-watcher.py # Patch script for proxy.py
├── src/
│   ├── PCH.h               # Precompiled header (CommonLibSSE-NG)
│   ├── main.cpp            # SKSE plugin entry point + logging
│   ├── proxy_launcher.h    # Launch result enum
│   └── proxy_launcher.cpp  # Pure Win32 process launcher (no CommonLibSSE deps)
└── Data/SKSE/Plugins/
    └── ProxyLauncher.ini   # Runtime config (paths + port)
```

## Dependencies (auto-downloaded by CMake)

- [CommonLibSSE-NG](https://github.com/CharmedBaryon/CommonLibSSE-NG) v3.7.0
- [fmt](https://github.com/fmtlib/fmt) 10.2.1
- [spdlog](https://github.com/gabime/spdlog) 1.13.0
- [rapidcsv](https://github.com/d99kris/rapidcsv) v8.83

## Feedback & Issues

Bug reports, suggestions, and questions are tracked on GitHub — please [open an issue](https://github.com/awesmdiver/ProxyLauncher/issues) so nothing gets lost.

## License

[MIT](LICENSE) — free to use, modify, and distribute for any purpose.