// SkyrimNetMultiProxy - SKSE Plugin
// Launches SkyrimNet MultiProxy on game startup.
// Config: Data/SKSE/Plugins/SkyrimNetMultiProxy.ini
//
// Plain Windows code: no CommonLibSSE-NG, no Address Library, no game structs. That is what lets
// one DLL load on Skyrim SE, AE, 1.7.x and Skyrim VR (see skse_plugin_api.h for how each SKSE
// finds the entry points below).

#define WIN32_LEAN_AND_MEAN
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <windows.h>

#include <format>
#include <string>
#include <thread>
#include <vector>

#include "plugin_support.h"
#include "proxy_launcher.h"
#include "multiproxy_channel.h"
#include "skse_plugin_api.h"

// ========================================
// Plugin Metadata
// ========================================

namespace {

// Same packing the SKSE plugin world has always used: major.minor.patch in one number.
constexpr std::uint32_t kPluginVersion =
    (SKYRIMNET_MULTIPROXY_VERSION_MAJOR & 0xFFu) << 24 |
    (SKYRIMNET_MULTIPROXY_VERSION_MINOR & 0xFFu) << 16 |
    (SKYRIMNET_MULTIPROXY_VERSION_PATCH & 0xFFFu) << 4;

constexpr void CopyText(char (&dest)[256], const char* text)
{
    for (std::size_t i = 0; text[i] != '\0' && i < 255; ++i) dest[i] = text[i];
}

constexpr skse_api::SKSEPluginVersionData MakeVersionData()
{
    skse_api::SKSEPluginVersionData d{};
    d.dataVersion = skse_api::SKSEPluginVersionData::kVersion;
    d.pluginVersion = kPluginVersion;
    CopyText(d.name, "SkyrimNetMultiProxy");
    CopyText(d.author, "awesmdiver");
    // Version independent and no Address Library: this mod uses no game structs and no game
    // addresses, so SKSE has nothing to check against the game's version. The flags
    // kVersionIndependent_AddressLibraryPostAE / _StructsPost629 are deliberately NOT set.
    d.versionIndependenceEx = skse_api::SKSEPluginVersionData::kVersionIndependentEx_NoStructUse;
    d.versionIndependence = skse_api::SKSEPluginVersionData::kVersionIndependent_Signatures;
    return d;
}

plug::Logger g_log;

void Log(const char* level, const std::string& text) { g_log.Write(level, text); }

// ========================================
// Logging
// ========================================

// Writes SkyrimNetMultiProxy.log into Documents\My Games\<Skyrim edition>\SKSE, the same place and
// file name as before; if that cannot be done, beside this DLL (and says so).
void InitializeLog(plug::Edition edition)
{
    const std::filesystem::path dllFolder = plug::DllFolder(reinterpret_cast<const void*>(&InitializeLog));
    plug::LogTarget target = plug::ResolveLogTarget(plug::DocumentsFolder(), edition, dllFolder);
    if (!g_log.Open(target.path)) {
        target = plug::ResolveLogTarget({}, edition, dllFolder, true);
        g_log.Open(target.path);
    }
    if (target.usedFallback) {
        Log("warn", std::format("Could not use the Documents\\My Games SKSE log folder; this log is beside the DLL instead: {}",
                                target.path.string()));
    }
}

// ========================================
// Error Display
// ========================================

// Opens a console window showing the error and waits for a keypress before closing.
// Used when LaunchProxy() fails so users don't have to hunt down the log file.
void ShowProxyError(const wchar_t* line1, const wchar_t* line2)
{
    std::wstring cmd =
        L"cmd.exe /c \"title SkyrimNetMultiProxy Error"
        L" & echo."
        L" & echo   SkyrimNetMultiProxy: ";
    cmd += line1;
    cmd += L" & echo   ";
    cmd += line2;
    cmd += L" & echo   (See SkyrimNetMultiProxy.log for details.)"
           L" & echo."
           L" & pause\"";

    std::vector<wchar_t> buf(cmd.begin(), cmd.end());
    buf.push_back(L'\0');

    STARTUPINFOW si{};
    si.cb          = sizeof(si);
    si.dwFlags     = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_SHOW;

    PROCESS_INFORMATION pi{};
    if (CreateProcessW(nullptr, buf.data(), nullptr, nullptr, FALSE,
                       CREATE_NEW_CONSOLE, nullptr, nullptr, &si, &pi)) {
        CloseHandle(pi.hProcess);
        CloseHandle(pi.hThread);
    }
}

// Called from proxy_launcher.cpp's own detached background thread (never the main/plugin-load
// thread) if a launched proxy never starts listening on its configured port within the startup
// grace window -- see ProxyStartupTimeoutCallback's own comment in proxy_launcher.h for why this
// is async. The logger takes a lock per line, so logging from this background thread is safe.
void LogProxyStartupTimeout(int port, int waitedSeconds)
{
    Log("error", std::format(
        "MultiProxy started but never began listening on port {} after {}s -- it may have "
        "crashed immediately. Check multiproxy.log (or proxy.log in an old setup) for details.",
        port, waitedSeconds));
}

// Log lines from the channel code (multiproxy_channel.cpp), which has no logger of its own.
// Called from the start-up thread.
void ChannelLog(int level, const char* text)
{
    Log(level == 0 ? "info" : level == 1 ? "warn" : "error", text);
}

}  // namespace

// ========================================
// Entry Points
// ========================================

// SKSE64 for Skyrim AE 1.6+ (and 1.7) reads this instead of calling SKSEPlugin_Query.
extern "C" __declspec(dllexport) constexpr skse_api::SKSEPluginVersionData SKSEPlugin_Version = MakeVersionData();

// SKSEVR and the old SKSE64 for Skyrim SE 1.5.97 call this first.
extern "C" __declspec(dllexport) bool SKSEPlugin_Query(const skse_api::SKSEInterface*, skse_api::PluginInfo* info)
{
    info->infoVersion = skse_api::PluginInfo::kInfoVersion;
    info->name        = "SkyrimNetMultiProxy";
    info->version     = kPluginVersion;
    return true;
}

extern "C" __declspec(dllexport) bool SKSEPlugin_Load(const skse_api::SKSEInterface*)
{
    const std::wstring exePath = plug::ModulePathOf(nullptr);
    const plug::Edition edition = plug::EditionFromExePath(exePath);
    InitializeLog(edition);

    Log("info", "SkyrimNetMultiProxy v" SKYRIMNET_MULTIPROXY_VERSION_STRING SKYRIMNET_MULTIPROXY_VERSION_LABEL " by awesmdiver");
    {
        const std::string exeName = std::filesystem::path(exePath).filename().string();
        const std::string version = plug::ReadFileVersion(exePath);
        Log("info", std::format("Game: {} {} ({})", exeName, version.empty() ? "(version not readable)" : version,
                                plug::EditionLabel(edition)));
    }

    // The installer's pointer file says MultiProxy is installed on this PC and how to reach it.
    // With it, the mod starts the tray app (hidden, outside any mod manager's job) and tells it
    // when Skyrim starts and closes. It never kills a process: the tray app decides what to do.
    mp::InstallInfo info;
    std::string whyNot;
    const std::wstring infoPath = mp::InstallInfoPath();
    const mp::ReadResult found = mp::ReadInstallInfo(infoPath, info, whyNot);

    if (found == mp::ReadResult::Ok) {
        Log("info", std::format("MultiProxy is installed (port {}); starting it outside the game if needed", info.port));
        const std::wstring gameFolder = GetGameFolder();
        // On a thread of its own: starting MultiProxy can take up to a minute and must never hold up Skyrim's loading.
        std::thread([info, gameFolder] {
            mp::RunStartup(info, gameFolder, SKYRIMNET_MULTIPROXY_VERSION_STRING, ChannelLog);
        }).detach();
    } else {
        if (found == mp::ReadResult::Unreadable) {
            Log("warn", std::format("The MultiProxy install file exists but could not be used ({}). Run the MultiProxy installer again.", whyNot));
        }

        // No usable pointer file: either the mod came in through a mod manager and the installer
        // was never run, or this is an old setup that points at the program in the ini. The old way
        // still works when the ini names a program that exists; otherwise stay quiet.
        bool usedLegacyIni = false;
        switch (LaunchProxy(&usedLegacyIni, LogProxyStartupTimeout)) {
            case ProxyLaunchResult::Launched:
                if (usedLegacyIni) {
                    Log("info", "Carried settings forward from the old ProxyLauncher.ini into SkyrimNetMultiProxy.ini");
                }
                Log("info", "MultiProxy launched (old way, from SkyrimNetMultiProxy.ini)");
                break;
            case ProxyLaunchResult::AlreadyRunning:
                if (usedLegacyIni) {
                    Log("info", "Carried settings forward from the old ProxyLauncher.ini into SkyrimNetMultiProxy.ini");
                }
                Log("info", "MultiProxy already running on the configured port -- skipping launch");
                break;
            case ProxyLaunchResult::NotConfigured:
                Log("info", "MultiProxy is not installed on this PC (no install file, and SkyrimNetMultiProxy.ini names no program); nothing to start");
                break;
            case ProxyLaunchResult::Failed:
                Log("error", "Failed to launch MultiProxy -- check SkyrimNetMultiProxy.ini paths");
                ShowProxyError(
                    L"Failed to start MultiProxy.",
                    L"Check ProxyScript and WorkDir in Data\\SKSE\\Plugins\\SkyrimNetMultiProxy.ini"
                );
                break;
        }
    }

    if (IsLegacyPluginDllPresent()) {
        Log("warn",
            "Data/SKSE/Plugins/ProxyLauncher.dll is still here -- this mod has been renamed to "
            "SkyrimNetMultiProxy.dll. You can delete the old ProxyLauncher.dll and ProxyLauncher.ini "
            "whenever you're ready; they're no longer used.");
    }

    return true;
}
