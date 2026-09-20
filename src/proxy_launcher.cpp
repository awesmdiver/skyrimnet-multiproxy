// Pure Win32 — must NOT include CommonLibSSE / PCH headers.
// Winsock2 must come before <windows.h>; the separate TU prevents conflicts
// with CommonLibSSE's SKSE::WinAPI wrappers in the other translation unit.
#define WIN32_LEAN_AND_MEAN
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <windows.h>

#include <chrono>
#include <string>
#include <thread>
#include <vector>

#include "proxy_launcher.h"

// Token-pastes SKYRIMNET_MULTIPROXY_VERSION_STRING (a plain narrow-char string literal, e.g.
// "3.0.0", defined via CMakeLists.txt's target_compile_definitions -- see that file's own comment
// for why PROJECT_VERSION is the single source of truth this reuses) into a wide string literal at
// compile time -- L##x only works when x is a literal token, which is exactly what a macro
// expanding to a string literal gives.
#define SNMP_WIDEN2(x) L##x
#define SNMP_WIDEN(x) SNMP_WIDEN2(x)

// ---- helpers ----------------------------------------------------------------

static std::wstring GetGameDir()
{
    wchar_t buf[MAX_PATH] = {};
    GetModuleFileNameW(nullptr, buf, MAX_PATH);
    std::wstring p(buf);
    auto sep = p.rfind(L'\\');
    return (sep != std::wstring::npos) ? p.substr(0, sep + 1) : p;
}

static std::wstring ReadIni(const wchar_t* section, const wchar_t* key,
                              const wchar_t* def, const std::wstring& path)
{
    wchar_t buf[1024] = {};
    GetPrivateProfileStringW(section, key, def, buf,
                              static_cast<DWORD>(std::size(buf)), path.c_str());
    return buf;
}

static bool FileExists(const std::wstring& path)
{
    DWORD attrs = GetFileAttributesW(path.c_str());
    return attrs != INVALID_FILE_ATTRIBUTES && !(attrs & FILE_ATTRIBUTE_DIRECTORY);
}

static bool IsPortListening(int port)
{
    WSADATA wsa{};
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return false;

    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) { WSACleanup(); return false; }

    // Non-blocking connect + select()-based timeout -- a plain blocking connect() to a REFUSED
    // loopback port is not reliably bounded by SO_SNDTIMEO (that governs send(), not connect()).
    // Measured live on a real dev machine: a refused 127.0.0.1 connection took ~2 real seconds to
    // return WSAECONNREFUSED, not the near-instant RST a refused loopback connection is commonly
    // assumed to give (likely a side effect of a virtual network adapter -- WSL2/Hyper-V -- on
    // that machine intercepting loopback traffic). That silently turned this function's intended
    // ~500ms bound into ~2s per call everywhere it's used, including the pre-existing "already
    // running" check this file already made before this comment was written. select() with an
    // explicit timeout is bounded by construction, independent of whatever the OS/network stack
    // actually does under the hood.
    u_long nonBlocking = 1;
    ioctlsocket(s, FIONBIO, &nonBlocking);

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port   = htons(static_cast<u_short>(port));
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);

    connect(s, reinterpret_cast<sockaddr*>(&addr), sizeof(addr));  // expected: WSAEWOULDBLOCK

    fd_set writeSet, exceptSet;
    FD_ZERO(&writeSet);
    FD_SET(s, &writeSet);
    FD_ZERO(&exceptSet);
    FD_SET(s, &exceptSet);
    timeval timeout{0, 500 * 1000};  // 500ms -- nfds (1st arg) is ignored on Windows, per MSDN

    bool up = false;
    // Windows Sockets signals a failed non-blocking connect via exceptfds, not writefds (unlike
    // POSIX, which signals writefds for both success and failure and requires a getsockopt(SO_ERROR)
    // to tell them apart) -- so writeSet-without-exceptSet is the correct Windows success check.
    if (select(0, nullptr, &writeSet, &exceptSet, &timeout) > 0 &&
        FD_ISSET(s, &writeSet) && !FD_ISSET(s, &exceptSet)) {
        up = true;
    }

    closesocket(s);
    WSACleanup();
    return up;
}

// Runs on its own detached thread (see LaunchProxy's own call site) -- polls until the port comes
// up or the grace window elapses, then reports via the caller-supplied callback. Never blocks
// SKSEPluginLoad: this thread is spawned and immediately forgotten, not joined.
static void WatchForStartupTimeout(int port, ProxyStartupTimeoutCallback callback)
{
    constexpr int kPollIntervalMs = 500;
    constexpr int kMaxWaitSeconds = 20;  // real startup takes ~4-5s even when everything works
                                          // (the Claude auth-capture subprocess call adds to it,
                                          // confirmed live) -- this leaves a generous margin
                                          // before treating a slow start as a real failure.
    // Tracks REAL elapsed wall-clock time rather than counting fixed kPollIntervalMs steps --
    // IsPortListening() itself is NOT free (bounded at up to ~500ms by its own select() timeout,
    // measured live at consistently ~500ms on a machine where a refused connect() would otherwise
    // take ~2s). A naive step counter that assumes each iteration costs only the sleep would
    // silently double the real total wait to ~40s instead of the intended ~20s.
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::seconds(kMaxWaitSeconds);
    while (std::chrono::steady_clock::now() < deadline) {
        std::this_thread::sleep_for(std::chrono::milliseconds(kPollIntervalMs));
        if (IsPortListening(port)) return;  // started fine, nothing to report
    }
    if (callback) callback(port, kMaxWaitSeconds);
}

// ---- public entry point -----------------------------------------------------

ProxyLaunchResult LaunchProxy(bool* usedLegacyIni, ProxyStartupTimeoutCallback onStartupTimeout)
{
    const std::wstring pluginsDir = GetGameDir() + L"Data\\SKSE\\Plugins\\";
    const std::wstring newIniPath = pluginsDir + L"SkyrimNetMultiProxy.ini";
    const std::wstring oldIniPath = pluginsDir + L"ProxyLauncher.ini";

    std::wstring iniPath = newIniPath;
    bool legacy = false;
    if (!FileExists(newIniPath) && FileExists(oldIniPath)) {
        // Carry an existing install's settings forward. Copy (rather than just reading the old
        // path forever) so SkyrimNetMultiProxy.ini -- the canonical name from here on -- actually
        // holds the real settings: a user who opens it to check/edit their paths finds them there,
        // and every later launch reads the same, single source of truth instead of silently
        // special-casing the legacy filename on every single startup.
        if (CopyFileW(oldIniPath.c_str(), newIniPath.c_str(), FALSE)) {
            iniPath = newIniPath;
        } else {
            iniPath = oldIniPath;  // couldn't copy (e.g. permissions) -- read the old one directly
        }
        legacy = true;
    }
    if (usedLegacyIni) *usedLegacyIni = legacy;

    std::wstring pythonExe   = ReadIni(L"General", L"PythonExe",   L"python", iniPath);
    std::wstring proxyScript = ReadIni(L"General", L"ProxyScript", L"",       iniPath);
    std::wstring workDir     = ReadIni(L"General", L"WorkDir",     L"",       iniPath);

    if (proxyScript.empty())
        return ProxyLaunchResult::Failed;

    wchar_t portBuf[16] = {};
    GetPrivateProfileStringW(L"General", L"Port", L"8000", portBuf,
                              static_cast<DWORD>(std::size(portBuf)), iniPath.c_str());
    int port = _wtoi(portBuf);

    if (IsPortListening(port))
        return ProxyLaunchResult::AlreadyRunning;

    // --plugin-version lets proxy.py show which plugin build launched it, right on the dashboard,
    // so a version mismatch between the two (this exact bug -- see CMakeLists.txt's own comment on
    // PROJECT_VERSION) is visible at a glance instead of silently drifting again. Additive: a
    // proxy.py that doesn't understand this argument (or was launched without going through this
    // plugin at all -- start-proxy.bat, a bare `py proxy.py`) simply never sees it, no behavior
    // change either way.
    std::wstring cmdLine = L"\"" + pythonExe + L"\" \"" + proxyScript + L"\""
        + (L" --plugin-version=" SNMP_WIDEN(SKYRIMNET_MULTIPROXY_VERSION_STRING));
    std::vector<wchar_t> cmd(cmdLine.begin(), cmdLine.end());
    cmd.push_back(L'\0');

    STARTUPINFOW si{};
    si.cb          = sizeof(si);
    si.dwFlags     = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_SHOWMINNOACTIVE; // minimised, non-stealing

    PROCESS_INFORMATION pi{};
    BOOL ok = CreateProcessW(
        nullptr, cmd.data(),
        nullptr, nullptr, FALSE,
        CREATE_NEW_CONSOLE, nullptr,
        workDir.empty() ? nullptr : workDir.c_str(),
        &si, &pi
    );

    if (!ok) return ProxyLaunchResult::Failed;

    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);

    // CreateProcessW succeeding only means Windows created a process image -- it says nothing
    // about whether that process actually did anything real (a broken PythonExe resolving to
    // Windows' Store alias stub is the confirmed real case: it exits almost instantly with no
    // output, and CreateProcessW still reports success). Detached and fire-and-forget so this
    // never blocks SKSEPluginLoad -- the common, working case pays zero added startup cost.
    if (onStartupTimeout) {
        std::thread(WatchForStartupTimeout, port, onStartupTimeout).detach();
    }

    return ProxyLaunchResult::Launched;
}

bool IsLegacyPluginDllPresent()
{
    return FileExists(GetGameDir() + L"Data\\SKSE\\Plugins\\ProxyLauncher.dll");
}
