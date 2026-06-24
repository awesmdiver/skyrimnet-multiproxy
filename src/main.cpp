// ProxyLauncher - SKSE Plugin
// Launches the Claude SkyrimNet proxy on game startup.
// Config: Data/SKSE/Plugins/ProxyLauncher.ini

#include "PCH.h"
#include "proxy_launcher.h"

#include <ShlObj.h>
#pragma comment(lib, "Shell32.lib")

// ========================================
// Plugin Metadata
// ========================================

// PluginDeclaration with default RuntimeCompatibility{} = version-independent
SKSEPluginInfo(
    .Version = { 1, 0, 0, 0 },
    .Name    = "ProxyLauncher",
    .Author  = "awesmdiver",
)

// ========================================
// Logging
// ========================================

// Writes to %USERPROFILE%\Documents\My Games\Skyrim Special Edition\SKSE\ProxyLauncher.log
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
        SKSE::stl::report_and_fail("ProxyLauncher: failed to resolve Documents folder for log path.");
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
        L"cmd.exe /c \"title ProxyLauncher Error"
        L" & echo."
        L" & echo   ProxyLauncher: ";
    cmd += line1;
    cmd += L" & echo   ";
    cmd += line2;
    cmd += L" & echo   (See ProxyLauncher.log for details.)"
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

// ========================================
// Entry Point
// ========================================

SKSEPluginLoad(const SKSE::LoadInterface* skse)
{
    InitializeLog();
    SKSE::Init(skse);
    SKSE::log::info("ProxyLauncher v1.0.0 by awesmdiver");

    switch (LaunchProxy()) {
        case ProxyLaunchResult::Launched:
            SKSE::log::info("Proxy launched successfully");
            break;
        case ProxyLaunchResult::AlreadyRunning:
            SKSE::log::info("Proxy already running on configured port — skipping launch");
            break;
        case ProxyLaunchResult::Failed:
            SKSE::log::error("Failed to launch proxy — check ProxyLauncher.ini paths");
            ShowProxyError(
                L"Failed to start the proxy.",
                L"Check ProxyScript and WorkDir in Data\\SKSE\\Plugins\\ProxyLauncher.ini"
            );
            break;
    }

    return true;
}
