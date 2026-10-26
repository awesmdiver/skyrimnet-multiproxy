// Built together with ../../src/multiproxy_channel.cpp into exit_probe_dll.dll, so the DllMain that
// sends "closed" at process exit is the real one. Exports one function for exit_probe.exe.
#include <windows.h>

#include <string>

#include "multiproxy_channel.h"

// logPath (optional): a file to give the exit notice, the way the mod gives it its own log.
extern "C" __declspec(dllexport) int ArmForTest(const wchar_t* logPath)
{
    mp::InstallInfo info;
    std::string why;
    if (mp::ReadInstallInfo(mp::InstallInfoPath(), info, why) != mp::ReadResult::Ok) return 1;
    if (!mp::PostStarted(info, L"C:\\Fake Skyrim\\", "test", GetCurrentProcessId())) return 1;
    if (logPath && *logPath) {
        HANDLE f = CreateFileW(logPath, GENERIC_WRITE, FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, CREATE_ALWAYS,
                               FILE_ATTRIBUTE_NORMAL, nullptr);
        if (f == INVALID_HANDLE_VALUE) return 1;
        const char first[] = "[test] first line\n";
        DWORD w = 0;
        WriteFile(f, first, sizeof(first) - 1, &w, nullptr);
        mp::SetExitLogHandle(f);
        CloseHandle(f);  // like the host's exit() closing the CRT stream: the notice keeps its own copy
    }
    mp::ArmExitNotice(info);
    return 0;
}
