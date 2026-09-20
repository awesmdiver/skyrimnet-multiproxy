#pragma once

enum class ProxyLaunchResult { Launched, AlreadyRunning, Failed };

// Invoked from a DETACHED BACKGROUND THREAD -- never the thread that called LaunchProxy -- if a
// launched proxy never starts listening on its configured port within the startup grace window.
// Catches any instant-death cause (a broken PythonExe, a bad path, a Python-level crash, ...) that
// CreateProcessW's own success can't see: `python` silently resolving to Windows' Store alias stub
// is the confirmed real case this fixes, but the check is general -- any cause that keeps the
// process from ever binding the port ends up here, not just that one. Only fires for the Launched
// case below; AlreadyRunning and Failed are already fully resolved synchronously.
// A plain C function pointer, not std::function -- keeps this pure-Win32 header from needing
// anything beyond the language itself.
using ProxyStartupTimeoutCallback = void (*)(int port, int waitedSeconds);

// Reads config from SkyrimNetMultiProxy.ini (Data/SKSE/Plugins/) and launches proxy.py.
// Falls back to reading an existing ProxyLauncher.ini (the pre-rename filename) if the new one
// doesn't exist yet -- lets an upgrading install keep working without re-running setup.
// Pure Win32 implementation — does not depend on CommonLibSSE headers. onStartupTimeout is the
// one deliberate exception to "this file has no logging capability": it's a plain function
// pointer supplied by the caller (which DOES have logging), so the callback itself can log without
// pulling SKSE::log or any CommonLibSSE header into this translation unit.
// If usedLegacyIni is non-null, it's set to true when that fallback (and copy-forward) happened,
// so the caller (which DOES have logging) can tell the user.
ProxyLaunchResult LaunchProxy(bool* usedLegacyIni = nullptr,
                               ProxyStartupTimeoutCallback onStartupTimeout = nullptr);

// True if the pre-rename ProxyLauncher.dll is still sitting in Data/SKSE/Plugins/ alongside this
// plugin -- lets the caller warn the user it's dead weight and safe to delete.
bool IsLegacyPluginDllPresent();
