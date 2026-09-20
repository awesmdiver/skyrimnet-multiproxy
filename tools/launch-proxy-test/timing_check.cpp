// Diagnostic tool, not a pass/fail test -- measures real wall-clock time for repeated
// IsPortListening() calls against a port nothing is listening on. Built while investigating why
// LaunchProxy()'s async startup-timeout callback wasn't firing within its documented ~20s window
// (2026-09-20): a plain blocking connect() to a REFUSED loopback port took ~2 real seconds to
// return on this machine (likely a virtual network adapter -- WSL2/Hyper-V -- intercepting
// loopback traffic), not the near-instant RST a refused loopback connection is commonly assumed
// to give. SO_SNDTIMEO does not bound connect()'s own timeout at all -- it governs send().
//
// Re-run this if IsPortListening() (or its non-blocking-connect-with-select() replacement) is
// ever touched again, to confirm it's still actually bounded near its intended ~500ms on
// whatever machine you're testing on -- this exact assumption silently broke once already.
//
// Usage: timing_check.exe [port]  (default port 18765, chosen to be unlikely to collide with
// anything real)
#define WIN32_LEAN_AND_MEAN
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <cstdio>
#include <cstdlib>

// Mirrors src/proxy_launcher.cpp's IsPortListening() exactly -- kept as a separate copy
// deliberately (this is a throwaway diagnostic tool, not linked against the real project).
static bool IsPortListening(int port)
{
    WSADATA wsa{};
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return false;

    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) { WSACleanup(); return false; }

    u_long nonBlocking = 1;
    ioctlsocket(s, FIONBIO, &nonBlocking);

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port   = htons(static_cast<u_short>(port));
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);

    connect(s, reinterpret_cast<sockaddr*>(&addr), sizeof(addr));

    fd_set writeSet, exceptSet;
    FD_ZERO(&writeSet);
    FD_SET(s, &writeSet);
    FD_ZERO(&exceptSet);
    FD_SET(s, &exceptSet);
    timeval timeout{0, 500 * 1000};

    bool up = false;
    if (select(0, nullptr, &writeSet, &exceptSet, &timeout) > 0 &&
        FD_ISSET(s, &writeSet) && !FD_ISSET(s, &exceptSet)) {
        up = true;
    }

    closesocket(s);
    WSACleanup();
    return up;
}

int main(int argc, char** argv) {
    int port = argc > 1 ? atoi(argv[1]) : 18765;
    LARGE_INTEGER freq, t0, t1;
    QueryPerformanceFrequency(&freq);

    for (int i = 0; i < 10; ++i) {
        QueryPerformanceCounter(&t0);
        bool up = IsPortListening(port);
        QueryPerformanceCounter(&t1);
        double ms = (t1.QuadPart - t0.QuadPart) * 1000.0 / freq.QuadPart;
        printf("iter %2d: IsPortListening(%d) took %.1f ms (result=%d)\n", i, port, ms, (int)up);
        fflush(stdout);
        Sleep(200);
    }
    return 0;
}
