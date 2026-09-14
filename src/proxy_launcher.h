#pragma once

enum class ProxyLaunchResult { Launched, AlreadyRunning, Failed };

// Reads config from SkyrimNetMultiProxy.ini (Data/SKSE/Plugins/) and launches proxy.py.
// Falls back to reading an existing ProxyLauncher.ini (the pre-rename filename) if the new one
// doesn't exist yet -- lets an upgrading install keep working without re-running setup.
// Pure Win32 implementation — does not depend on CommonLibSSE headers.
// If usedLegacyIni is non-null, it's set to true when that fallback (and copy-forward) happened,
// so the caller (which DOES have logging) can tell the user.
ProxyLaunchResult LaunchProxy(bool* usedLegacyIni = nullptr);

// True if the pre-rename ProxyLauncher.dll is still sitting in Data/SKSE/Plugins/ alongside this
// plugin -- lets the caller warn the user it's dead weight and safe to delete.
bool IsLegacyPluginDllPresent();
