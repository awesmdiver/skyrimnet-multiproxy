// SkyrimNetMultiProxy - SKSE Plugin
// Launches SkyrimNet MultiProxy on game startup.
// Config: Data/SKSE/Plugins/SkyrimNetMultiProxy.ini

#include "PCH.h"
#include "proxy_launcher.h"
#include "multiproxy_channel.h"

#include <thread>

#include <ShlObj.h>
#pragma comment(lib, "Shell32.lib")

// ========================================
// Plugin Metadata
// ========================================

// PluginDeclaration with default RuntimeCompatibility{} = version-independent.
// .Version is driven by CMakeLists.txt's PROJECT_VERSION (via the MAJOR/MINOR/PATCH macros it
// defines) rather than a hand-written literal -- it used to be hardcoded at { 2, 0, 0, 0 } and
// silently stopped matching the shipped build after the very first release.
SKSEPluginInfo(
    .Version = { SKYRIMNET_MULTIPROXY_VERSION_MAJOR, SKYRIMNET_MULTIPROXY_VERSION_MINOR, SKYRIMNET_MULTIPROXY_VERSION_PATCH, 0 },
    .Name    = "SkyrimNetMultiProxy",
    .Author  = "awesmdiver",
)

// ========================================
// Logging
// ========================================

// Writes to %USERPROFILE%\Documents\My Games\Skyrim Special Edition\SKSE\SkyrimNetMultiProxy.log
static void InitializeLog()
{
    // Resolve Documents folder via shell API so OneDrive redirection is handled correctly
    PWSTR docs = nullptr;
    std::filesystem::path logDir;
    if (SUCCEEDED(::SHGetKnownFolderPath(FOLDERID_Documents, 0, nullptr, &docs)) && docs) {
        logDir = docs;
    }
    if (docs) {
        ::CoTaskMemFree(docs);
    }

    if (logDir.empty()) {
        SKSE::stl::report_and_fail("SkyrimNetMultiProxy: failed to resolve Documents folder for log path.");
    }

    logDir /= "My Games";
    logDir /= "Skyrim Special Edition";
    logDir /= "SKSE";

    std::error_code ec;
    std::filesystem::create_directories(logDir, ec);

    const auto logPath = logDir / fmt::format("{}.log", SKSE::PluginDeclaration::GetSingleton()->GetName());

    auto sink = std::make_shared<spdlog::sinks::basic_file_sink_mt>(logPath.string(), true);
    auto log  = std::make_shared<spdlog::logger>("global", std::move(sink));
    log->set_level(spdlog::level::info);
    log->flush_on(spdlog::level::info);
    spdlog::set_default_logger(std::move(log));
    spdlog::set_pattern("[%Y-%m-%d %T] [%l] %v");
}

// ========================================
// Error Display
// ========================================

// Opens a console window showing the error and waits for a keypress before closing.
// Used when LaunchProxy() fails so users don't have to hunt down the log file.
static void ShowProxyError(const wchar_t* line1, const wchar_t* line2)
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
// is async. spdlog's file sink is thread-safe (basic_file_sink_mt, set up in InitializeLog above),
// so logging from this background thread is safe.
static void LogProxyStartupTimeout(int port, int waitedSeconds)
{
    SKSE::log::error(
        "MultiProxy started but never began listening on port {} after {}s -- it may have "
        "crashed immediately. Check multiproxy.log (or proxy.log in an old setup) for details.",
        port, waitedSeconds
    );
}

// Log lines from the channel code (multiproxy_channel.cpp), which has no logger of its own.
// Called from the start-up thread; spdlog's file sink is thread-safe.
static void ChannelLog(int level, const char* text)
{
    switch (level) {
        case 0: SKSE::log::info("{}", text); break;
        case 1: SKSE::log::warn("{}", text); break;
        default: SKSE::log::error("{}", text); break;
    }
}

// ========================================
// Entry Point
// ========================================

SKSEPluginLoad(const SKSE::LoadInterface* skse)
{
    InitializeLog();
    SKSE::Init(skse);
    SKSE::log::info("SkyrimNetMultiProxy v" SKYRIMNET_MULTIPROXY_VERSION_STRING " by awesmdiver");

    // The installer's pointer file says MultiProxy is installed on this PC and how to reach it.
    // With it, the mod starts the tray app (hidden, outside any mod manager's job) and tells it
    // when Skyrim starts and closes. It never kills a process: the tray app decides what to do.
    mp::InstallInfo info;
    std::string whyNot;
    const std::wstring infoPath = mp::InstallInfoPath();
    const mp::ReadResult found = mp::ReadInstallInfo(infoPath, info, whyNot);

    if (found == mp::ReadResult::Ok) {
        SKSE::log::info("MultiProxy is installed (port {}); starting it outside the game if needed", info.port);
        const std::wstring gameFolder = GetGameFolder();
        // On a thread of its own: starting MultiProxy can take up to a minute and must never hold up Skyrim's loading.
        std::thread([info, gameFolder] {
            mp::RunStartup(info, gameFolder, SKYRIMNET_MULTIPROXY_VERSION_STRING, ChannelLog);
        }).detach();
    } else {
        if (found == mp::ReadResult::Unreadable) {
            SKSE::log::warn("The MultiProxy install file exists but could not be used ({}). Run the MultiProxy installer again.", whyNot);
        }

        // No usable pointer file: either the mod came in through a mod manager and the installer
        // was never run, or this is an old setup that points at the program in the ini. The old way
        // still works when the ini names a program that exists; otherwise stay quiet.
        bool usedLegacyIni = false;
        switch (LaunchProxy(&usedLegacyIni, LogProxyStartupTimeout)) {
            case ProxyLaunchResult::Launched:
                if (usedLegacyIni) {
                    SKSE::log::info("Carried settings forward from the old ProxyLauncher.ini into SkyrimNetMultiProxy.ini");
                }
                SKSE::log::info("MultiProxy launched (old way, from SkyrimNetMultiProxy.ini)");
                break;
            case ProxyLaunchResult::AlreadyRunning:
                if (usedLegacyIni) {
                    SKSE::log::info("Carried settings forward from the old ProxyLauncher.ini into SkyrimNetMultiProxy.ini");
                }
                SKSE::log::info("MultiProxy already running on the configured port — skipping launch");
                break;
            case ProxyLaunchResult::NotConfigured:
                SKSE::log::info("MultiProxy is not installed on this PC (no install file, and SkyrimNetMultiProxy.ini names no program); nothing to start");
                break;
            case ProxyLaunchResult::Failed:
                SKSE::log::error("Failed to launch MultiProxy — check SkyrimNetMultiProxy.ini paths");
                ShowProxyError(
                    L"Failed to start MultiProxy.",
                    L"Check ProxyScript and WorkDir in Data\\SKSE\\Plugins\\SkyrimNetMultiProxy.ini"
                );
                break;
        }
    }

    if (IsLegacyPluginDllPresent()) {
        SKSE::log::warn(
            "Data/SKSE/Plugins/ProxyLauncher.dll is still here -- this mod has been renamed to "
            "SkyrimNetMultiProxy.dll. You can delete the old ProxyLauncher.dll and ProxyLauncher.ini "
            "whenever you're ready; they're no longer used."
        );
    }

    return true;
}
