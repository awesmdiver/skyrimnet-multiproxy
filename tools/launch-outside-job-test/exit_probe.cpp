// Tests the "Skyrim closed" notice. Loads exit_probe_dll.dll (the mod's REAL channel code with its
// real DllMain), lets it send "started", then ends the process the way asked:
//
//   exit_probe.exe <ExitProcess|TerminateProcess|return|exit> [preload] [threads] [log=<file>] [dll=<name>]
//
// ExitProcess / return / exit: DLLs get DLL_PROCESS_DETACH, so "closed" may reach MultiProxy.
// TerminateProcess:     nothing in the process runs, so no notice is possible.
// preload:              start Winsock (and make one socket) BEFORE the DLL loads. Windows detaches
//                       DLLs in reverse load order, and Winsock's own provider DLLs load on the first
//                       socket: loaded after the mod's DLL, they are already gone when its DllMain
//                       runs at exit and every socket call fails.
// threads:              leave a few threads running (like a game's worker threads) when the process ends.
// log=<file>:           give the exit notice that file; it should end with one "Skyrim is closing" line.
// dll=<name>:           which build of the DLL to load (default exit_probe_dll.dll).
#include <winsock2.h>
#include <windows.h>

#include <cstdio>
#include <cstdlib>
#include <cwchar>
#include <string>

static DWORD WINAPI Spin(LPVOID)
{
    for (;;) Sleep(5);
}

int wmain(int argc, wchar_t** argv)
{
    const wchar_t* how = argc > 1 ? argv[1] : L"ExitProcess";
    bool preload = false, threads = false;
    std::wstring log;
    std::wstring dllName = L"exit_probe_dll.dll";
    for (int i = 2; i < argc; ++i) {
        const std::wstring a = argv[i];
        if (a == L"preload") preload = true;
        else if (a == L"threads") threads = true;
        else if (a.rfind(L"log=", 0) == 0) log = a.substr(4);
        else if (a.rfind(L"dll=", 0) == 0) dllName = a.substr(4);
    }
    if (preload) {
        WSADATA wsa{};
        WSAStartup(MAKEWORD(2, 2), &wsa);
        closesocket(socket(AF_INET, SOCK_STREAM, IPPROTO_TCP));
    }
    HMODULE dll = LoadLibraryW(dllName.c_str());
    if (!dll) { std::printf("could not load %ls (error %lu)\n", dllName.c_str(), GetLastError()); return 2; }
    auto arm = reinterpret_cast<int (*)(const wchar_t*)>(GetProcAddress(dll, "ArmForTest"));
    if (!arm || arm(log.c_str()) != 0) { std::printf("arming failed\n"); return 2; }
    if (threads) for (int i = 0; i < 4; ++i) CreateThread(nullptr, 0, Spin, nullptr, 0, nullptr);
    std::printf("[exit_probe] started sent; ending the process with %ls\n", how);
    std::fflush(stdout);
    if (std::wcscmp(how, L"TerminateProcess") == 0) TerminateProcess(GetCurrentProcess(), 0);
    else if (std::wcscmp(how, L"return") == 0) return 0;
    else if (std::wcscmp(how, L"exit") == 0) std::exit(0);
    ExitProcess(0);
}
