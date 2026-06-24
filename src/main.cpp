// ProxyLauncher - SKSE Plugin
// Launches the Claude SkyrimNet proxy on game startup.
// Config: Data/SKSE/Plugins/ProxyLauncher.ini

#include "PCH.h"
#include "proxy_launcher.h"

// PluginDeclaration with default RuntimeCompatibility{} = version-independent
// (StructCompatibility defaults to Independent as well)
SKSEPluginInfo(
    .Version = { 1, 0, 0, 0 },
    .Name    = "ProxyLauncher",
    .Author  = "awesmdiver",
)

SKSEPluginLoad(const SKSE::LoadInterface* skse)
{
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
            break;
    }

    return true;
}
