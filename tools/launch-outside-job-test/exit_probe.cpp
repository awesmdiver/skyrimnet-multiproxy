// Tests the "Skyrim closed" notice. Loads exit_probe_dll.dll (the mod's REAL channel code with its
// real DllMain), lets it send "started", then ends the process the way asked:
//
//   exit_probe.exe <ExitProcess|TerminateProcess|return> [preload]
//
// ExitProcess / return: DLLs get DLL_PROCESS_DETACH, so "closed" may reach MultiProxy.
// TerminateProcess:     nothing in the process runs, so no notice is possible.
// preload:              start Winsock (and make one socket) BEFORE the DLL loads. Windows detaches
//                       DLLs in reverse load order, and Winsock's own provider DLLs load on the first
//                       socket: loaded after the mod's DLL, they are already gone when its DllMain
//                       runs at exit and every socket call fails.
#include <winsock2.h>
#include <windows.h>

#include <cstdio>
#include <cwchar>

int wmain(int argc, wchar_t** argv)
{
    const wchar_t* how = argc > 1 ? argv[1] : L"ExitProcess";
    if (argc > 2 && std::wcscmp(argv[2], L"preload") == 0) {
        WSADATA wsa{};
        WSAStartup(MAKEWORD(2, 2), &wsa);
        closesocket(socket(AF_INET, SOCK_STREAM, IPPROTO_TCP));
    }
    HMODULE dll = LoadLibraryW(L"exit_probe_dll.dll");
    if (!dll) { std::printf("could not load exit_probe_dll.dll (error %lu)\n", GetLastError()); return 2; }
    auto arm = reinterpret_cast<int (*)()>(GetProcAddress(dll, "ArmForTest"));
    if (!arm || arm() != 0) { std::printf("arming failed\n"); return 2; }
    std::printf("[exit_probe] started sent; ending the process with %ls\n", how);
    std::fflush(stdout);
    if (std::wcscmp(how, L"TerminateProcess") == 0) TerminateProcess(GetCurrentProcess(), 0);
    else if (std::wcscmp(how, L"return") == 0) return 0;
    ExitProcess(0);
}
