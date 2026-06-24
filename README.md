# ProxyLauncher

An SKSE plugin that automatically launches the **Claude SkyrimNet proxy** (`proxy.py`) when Skyrim starts via `skse64_loader.exe` — so you don't have to run it manually before every session.

## How it works

On `SKSEPlugin_Load` (very early, before the main menu), the plugin:

1. Checks whether port 8000 is already listening (so a manually pre-launched proxy is respected)
2. If not running, launches `python proxy.py` as a detached, minimised console process
3. Logs the result to `%LOCALAPPDATA%\Skyrim Special Edition\SKSE\ProxyLauncher.log`

The proxy warms up in the background while the game loads. The first NPC conversation will wait up to 60 s for auth to be ready (handled inside the proxy itself).

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

1. Build the DLL (see below) — or grab a release build
2. Copy `ProxyLauncher.dll` → `Data/SKSE/Plugins/`
3. Copy `ProxyLauncher.ini` → `Data/SKSE/Plugins/` and edit paths as needed
4. Launch Skyrim via SKSE (Vortex, MO2, or `skse64_loader.exe` directly)

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
