// Standalone isolated harness for LaunchProxy() (src/proxy_launcher.cpp) -- exercises the real
// launch + async startup-timeout logic without needing an actual running Skyrim, and without
// touching the live install's SkyrimNetMultiProxy.ini/.dll.
//
// LaunchProxy() finds "its own" ini via GetModuleFileNameW(nullptr, ...) -- i.e. wherever the
// currently running EXE lives -- so this only works when the compiled test exe has its own
// Data/SKSE/Plugins/SkyrimNetMultiProxy.ini sitting next to it (see README.md's own two
// scenarios: ini-success/ and ini-failure/).
//
// See README.md in this folder for build + run instructions and what each scenario proves.
#include <cstdio>
#include <windows.h>

#include "proxy_launcher.h"

static void OnStartupTimeout(int port, int waitedSeconds) {
    printf("[CALLBACK] port %d never came up after %ds\n", port, waitedSeconds);
    fflush(stdout);
}

int main() {
    LARGE_INTEGER freq, t0, t1;
    QueryPerformanceFrequency(&freq);
    QueryPerformanceCounter(&t0);

    ProxyLaunchResult result = LaunchProxy(nullptr, OnStartupTimeout);

    QueryPerformanceCounter(&t1);
    double elapsedMs = (t1.QuadPart - t0.QuadPart) * 1000.0 / freq.QuadPart;

    const char* resultStr =
        result == ProxyLaunchResult::Launched ? "Launched" :
        result == ProxyLaunchResult::AlreadyRunning ? "AlreadyRunning" : "Failed";
    printf("[MAIN] LaunchProxy() returned %s in %.1f ms\n", resultStr, elapsedMs);
    fflush(stdout);

    // The real window is 20s (see WatchForStartupTimeout's own kMaxWaitSeconds) -- 30s here
    // gives comfortable margin to observe the callback firing (or not) either way.
    printf("[MAIN] sleeping 30s to observe whether the async callback fires...\n");
    fflush(stdout);
    Sleep(30000);
    printf("[MAIN] done waiting\n");
    return 0;
}
