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

#include <string>
#include <vector>

#include "proxy_launcher.h"

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

static bool IsPortListening(int port)
{
    WSADATA wsa{};
    if (WSAStartup(MAKEWORD(2, 2), &wsa) != 0) return false;

    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) { WSACleanup(); return false; }

    DWORD timeout = 500; // ms
    setsockopt(s, SOL_SOCKET, SO_SNDTIMEO,
               reinterpret_cast<const char*>(&timeout), sizeof(timeout));

    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port   = htons(static_cast<u_short>(port));
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);

    bool up = (connect(s, reinterpret_cast<sockaddr*>(&addr), sizeof(addr)) == 0);
    closesocket(s);
    WSACleanup();
    return up;
}

// ---- public entry point -----------------------------------------------------

ProxyLaunchResult LaunchProxy()
{
    std::wstring iniPath = GetGameDir() + L"Data\\SKSE\\Plugins\\ProxyLauncher.ini";

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

    std::wstring cmdLine = L"\"" + pythonExe + L"\" \"" + proxyScript + L"\"";
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
    return ProxyLaunchResult::Launched;
}
