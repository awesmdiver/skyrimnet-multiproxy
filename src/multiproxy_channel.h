#pragma once
// The Skyrim mod's side of the local channel to a running MultiProxy (contract: the dev repo's
// TECHNICAL.md, "The contract for the Skyrim mod", and local_channel.py). Pure Win32 -- must NOT
// include CommonLibSSE / PCH headers (same reason as proxy_launcher.cpp: winsock2.h before
// windows.h, and a separate translation unit keeps it away from SKSE::WinAPI). Logging goes
// through a plain callback so this file needs no logging library, which is also what lets the
// test tools under tools/ link the REAL source instead of a copy.
//
// Nothing here ever kills a process. The tray app decides whether to quit.

#include <string>
#include <vector>

namespace mp {

// 0 = info, 1 = warning, 2 = error
using LogFn = void (*)(int level, const char* text);

struct InstallInfo {
    int port = 0;
    std::string token;
    std::vector<std::wstring> trayCommand;   // ["<pythonw.exe>", "<program folder>\multiproxy_tray.py"]
    std::wstring workingDirectory;
    std::wstring fromSkyrimArg;              // "--from-skyrim"
};

enum class ReadResult { Ok, Missing, Unreadable };

// %APPDATA%\SkyrimNet MultiProxy\install-info.json, or the file named by MULTIPROXY_INSTALL_INFO
// (the same override the MultiProxy side honours: tests, and anyone who needs it elsewhere).
std::wstring InstallInfoPath();

// Missing = no pointer file (installer never run). Unreadable = there, but not usable; whyNot says why.
ReadResult ReadInstallInfo(const std::wstring& path, InstallInfo& out, std::string& whyNot);

// One small HTTP/1.1 request to 127.0.0.1:<port> with the bearer token. True when a reply with a
// status line came back (status filled in); false on no connection or no reply inside timeoutMs.
bool HttpCall(const InstallInfo& info, const char* method, const char* path, const std::string& body,
              int timeoutMs, int* status, std::string* replyBody = nullptr);

// GET /local/ping answered 200.
bool Ping(const InstallInfo& info, int timeoutMs = 1500);

enum class TrayStart {
    BrokeAway,    // CreateProcess with CREATE_BREAKAWAY_FROM_JOB (or not in a job at all)
    ViaShell,     // the job forbids breakaway: Explorer started it for us
    InsideJob,    // last resort: started inside the job (a mod manager may keep waiting for it)
    Failed,
};

// Starts tray.command + fromSkyrimArg hidden and outside any job Skyrim runs in (Mod Organizer 2
// runs the game in one; whatever stays in it keeps MO2 locked after the game closes).
TrayStart StartTrayOutsideJob(const InstallInfo& info, LogFn log);

// Ping until it answers or timeoutMs passes.
bool WaitForPing(const InstallInfo& info, int timeoutMs);

// POST /local/skyrim/started. The folder is the game folder; pid lets the other end double-check.
bool PostStarted(const InstallInfo& info, const std::wstring& gameFolder, const char* modVersion,
                 unsigned long pid);

// POST /local/skyrim/closed.
bool PostClosed(const InstallInfo& info, int timeoutMs);

// Remembers the info so "closed" can be sent when the process ends (see DllMain in the .cpp).
void ArmExitNotice(const InstallInfo& info);

// The whole start-up sequence, meant for its own background thread: ping; start the tray app if
// nothing answers; wait up to 60 s; tell it Skyrim started. Never blocks the game's loading when
// run on a thread of its own.
void RunStartup(const InstallInfo& info, const std::wstring& gameFolder, const char* modVersion,
                LogFn log);

}  // namespace mp
