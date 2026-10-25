// Tests for src/plugin_support.h (log folder choice, edition detection, file version, logger) and,
// when given a DLL path, the built plugin's SKSE entry points. No game needed. Build + run: build.ps1
#define WIN32_LEAN_AND_MEAN
#define NOMINMAX
#include <winsock2.h>
#include <windows.h>

#include "plugin_support.h"
#include "skse_plugin_api.h"

#include <cstdio>
#include <fstream>

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) { std::printf("FAIL line %d: %s\n", __LINE__, #cond); ++failures; } \
    } while (0)

namespace fs = std::filesystem;

int wmain(int argc, wchar_t** argv)
{
    using plug::Edition;

    // --- edition from the exe name
    CHECK(plug::EditionFromExePath(L"C:\\Games\\Skyrim VR\\SkyrimVR.exe") == Edition::VR);
    CHECK(plug::EditionFromExePath(L"D:\\x\\skyrimvr.EXE") == Edition::VR);
    CHECK(plug::EditionFromExePath(L"C:\\Games\\Skyrim Special Edition\\SkyrimSE.exe") == Edition::SpecialEdition);
    CHECK(plug::EditionFromExePath(L"") == Edition::SpecialEdition);
    CHECK(plug::DocumentsFolderNames(Edition::VR).size() == 1 && plug::DocumentsFolderNames(Edition::VR)[0] == L"Skyrim VR");
    CHECK(plug::DocumentsFolderNames(Edition::SpecialEdition)[0] == L"Skyrim Special Edition");

    // --- log folder choice, in stand-in folders
    const fs::path root = fs::temp_directory_path() / "plugin-test";
    fs::remove_all(root);
    const fs::path docs = root / "Documents";
    const fs::path dll = root / "plugins";
    fs::create_directories(docs);
    fs::create_directories(dll);

    // Nothing exists yet: SE gets the plain folder name, VR gets "Skyrim VR", both created.
    auto se = plug::ResolveLogTarget(docs, Edition::SpecialEdition, dll);
    CHECK(!se.usedFallback);
    CHECK(se.path == docs / L"My Games" / L"Skyrim Special Edition" / L"SKSE" / L"SkyrimNetMultiProxy.log");
    auto vr = plug::ResolveLogTarget(docs, Edition::VR, dll);
    CHECK(!vr.usedFallback);
    CHECK(vr.path == docs / L"My Games" / L"Skyrim VR" / L"SKSE" / L"SkyrimNetMultiProxy.log");
    CHECK(fs::is_directory(docs / L"My Games" / L"Skyrim VR" / L"SKSE"));

    // A GOG install: only the GOG folder exists, so that is the one chosen for an SE exe.
    const fs::path docs2 = root / "Documents2";
    fs::create_directories(docs2 / L"My Games" / L"Skyrim Special Edition GOG" / L"SKSE");
    CHECK(plug::ChooseLogFolder(docs2, Edition::SpecialEdition) == docs2 / L"My Games" / L"Skyrim Special Edition GOG" / L"SKSE");

    // No Documents folder, or the caller forcing the fallback: the log goes beside the DLL, flagged.
    auto none = plug::ResolveLogTarget({}, Edition::VR, dll);
    CHECK(none.usedFallback && none.path == dll / L"SkyrimNetMultiProxy.log");
    auto forced = plug::ResolveLogTarget(docs, Edition::VR, dll, true);
    CHECK(forced.usedFallback && forced.path == dll / L"SkyrimNetMultiProxy.log");

    // A Documents folder that cannot hold the folders (a file sits where "My Games" should be).
    const fs::path docs3 = root / "Documents3";
    fs::create_directories(docs3);
    { std::ofstream(docs3 / "My Games") << "i am a file"; }
    auto blocked = plug::ResolveLogTarget(docs3, Edition::SpecialEdition, dll);
    CHECK(blocked.usedFallback && blocked.path == dll / L"SkyrimNetMultiProxy.log");

    // --- the logger writes the line format the support steps rely on
    plug::Logger log;
    CHECK(log.Open(se.path));
    log.Write("info", "SkyrimNetMultiProxy v0.0.0 by awesmdiver");
    log.Write("warn", "second line");
    {
        std::ifstream in(se.path);
        std::string l1, l2;
        std::getline(in, l1);
        std::getline(in, l2);
        CHECK(l1.size() > 30 && l1[0] == '[' && l1[5] == '-' && l1.find("] [info] SkyrimNetMultiProxy v0.0.0 by awesmdiver") == 20);
        if (l2.empty()) std::printf("l1=[%s] l2 empty\n", l1.c_str());
        CHECK(l2.find("] [warn] second line") != std::string::npos);
    }
    CHECK(!log.Open(root / "no-such-folder" / "x.log"));

    // --- game version from a real exe's file version (any system exe has one)
    wchar_t sys[MAX_PATH] = {};
    ::GetSystemDirectoryW(sys, MAX_PATH);
    const std::string v = plug::ReadFileVersion(std::wstring(sys) + L"\\notepad.exe");
    CHECK(!v.empty() && std::count(v.begin(), v.end(), '.') == 3);
    CHECK(plug::ReadFileVersion(L"C:\\no\\such\\file.exe").empty());

    // --- the built DLL's SKSE entry points (optional first argument: path to the DLL)
    if (argc > 1) {
        HMODULE h = ::LoadLibraryW(argv[1]);
        CHECK(h != nullptr);
        if (h) {
            const auto* ver = reinterpret_cast<const skse_api::SKSEPluginVersionData*>(::GetProcAddress(h, "SKSEPlugin_Version"));
            CHECK(ver != nullptr);
            CHECK(::GetProcAddress(h, "SKSEPlugin_Load") != nullptr);
            auto query = reinterpret_cast<bool (*)(const skse_api::SKSEInterface*, skse_api::PluginInfo*)>(::GetProcAddress(h, "SKSEPlugin_Query"));
            CHECK(query != nullptr);
            if (ver) {
                CHECK(ver->dataVersion == skse_api::SKSEPluginVersionData::kVersion);
                CHECK(std::string(ver->name) == "SkyrimNetMultiProxy");
                CHECK(std::string(ver->author) == "awesmdiver");
                CHECK(ver->versionIndependenceEx & skse_api::SKSEPluginVersionData::kVersionIndependentEx_NoStructUse);
                CHECK(!(ver->versionIndependence & skse_api::SKSEPluginVersionData::kVersionIndependent_AddressLibraryPostAE));
                CHECK(!(ver->versionIndependenceEx & skse_api::SKSEPluginVersionData::kVersionIndependentEx_AddressLibraryV5));
                CHECK(ver->compatibleVersions[0] == 0 && ver->seVersionRequired == 0);
                CHECK(ver->pluginVersion != 0);
            }
            if (query) {
                skse_api::PluginInfo info{};
                CHECK(query(nullptr, &info));
                CHECK(info.infoVersion == skse_api::PluginInfo::kInfoVersion && std::string(info.name) == "SkyrimNetMultiProxy");
                CHECK(ver && info.version == ver->pluginVersion);
            }
            // The mod must not need CommonLib or the Address Library: nothing of theirs is loaded with it.
            CHECK(::GetModuleHandleW(L"SkyrimNetMultiProxy.dll") == h);
            ::FreeLibrary(h);
        }
    }

    fs::remove_all(root);
    if (failures == 0) std::printf("plugin tests: all passed\n");
    return failures == 0 ? 0 : 1;
}
