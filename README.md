# ProxyLauncher

([galanx/Claude-SkyrimNet-Proxy](https://github.com/galanx/Claude-SkyrimNet-Proxy)) is a great way to leverage your Claude subscription with ([Minll/SkyrimNet-GamePlugin](https://github.com/MinLL/SkyrimNet-GamePlugin)). However, for me I always forgot start or stop the proxy so I decided to create an SKSE plugin that automatically launches the **Claude SkyrimNet proxy** (`proxy.py`) when Skyrim starts via `skse64_loader.exe` — so you don't have to run it manually before every session or exit it manually.

## How it works

On `SKSEPlugin_Load` (very early, before the main menu), the plugin:

1. Checks whether port 8000 is already listening (so a manually pre-launched proxy is respected)
2. If not running, launches `python proxy.py` as a detached, minimised console process
3. Logs the result to `Documents\My Games\Skyrim Special Edition\SKSE\ProxyLauncher.log`

The proxy warms up in the background while the game loads. The first NPC conversation will wait up to 60 s for auth to be ready (handled inside the proxy itself).

**Log files** (both in `Documents\My Games\Skyrim Special Edition\SKSE\`):

| File | Created by | Contains |
|------|-----------|----------|
| `ProxyLauncher.log` | SKSE plugin (always) | Launch status — in `Documents\My Games\Skyrim Special Edition\SKSE\` |
| `proxy.log` | proxy.py (`EnableLogging = true`) | Full proxy activity — alongside `proxy.py` |

## Configuration

Edit `Data/SKSE/Plugins/ProxyLauncher.ini` — no recompile needed:

```ini
[General]
; Path to the Python executable. "python" works if it's on your PATH.
PythonExe=python

; Full path to proxy.py
ProxyScript=C:\Users\your_account\.local\bin\proxy.py

; Working directory for the proxy process (where config.json lives)
WorkDir=C:\Users\your_account\.local\bin

; Port the proxy listens on — used to detect if it's already running
Port=8000
```

## Installation

**Via mod manager (recommended):** install `ProxyLauncher-v1.0.0.zip` through MO2 or Vortex — the files land in `Data/SKSE/Plugins/` automatically.

**Manually:**
1. Copy `ProxyLauncher.dll` → `Data/SKSE/Plugins/`
2. Copy `ProxyLauncher.ini` → `Data/SKSE/Plugins/` and edit paths as needed
3. Launch Skyrim via SKSE (Vortex, MO2, or `skse64_loader.exe` directly)

## Patching proxy.py: auto-close and file logging

The proxy ([galanx/Claude-SkyrimNet-Proxy](https://github.com/galanx/Claude-SkyrimNet-Proxy))
can be patched to add two optional features, both off by default and toggled
via `proxy.ini` next to the script.

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
- Creates `proxy.ini` alongside `proxy.py` if it doesn't exist

**Features added by the patch:**

| Option | Default | Description |
|--------|---------|-------------|
| `AutoCloseWithSkyrim` | `false` | Monitor the Skyrim process and shut the proxy down when the game exits |
| `EnableLogging` | `false` | Write `proxy.log` alongside `proxy.py` — fresh file each session |

**`proxy.ini` reference:**

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

## License

MIT
