#pragma once

enum class ProxyLaunchResult { Launched, AlreadyRunning, Failed };

// Reads config from ProxyLauncher.ini (Data/SKSE/Plugins/) and launches proxy.py.
// Pure Win32 implementation — does not depend on CommonLibSSE headers.
ProxyLaunchResult LaunchProxy();
