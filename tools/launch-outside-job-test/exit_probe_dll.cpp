// Built together with ../../src/multiproxy_channel.cpp into exit_probe_dll.dll, so the DllMain that
// sends "closed" at process exit is the real one. Exports one function for exit_probe.exe.
#include <windows.h>

#include <string>

#include "multiproxy_channel.h"

extern "C" __declspec(dllexport) int ArmForTest()
{
    mp::InstallInfo info;
    std::string why;
    if (mp::ReadInstallInfo(mp::InstallInfoPath(), info, why) != mp::ReadResult::Ok) return 1;
    if (!mp::PostStarted(info, L"C:\\Fake Skyrim\\", "test", GetCurrentProcessId())) return 1;
    mp::ArmExitNotice(info);
    return 0;
}
