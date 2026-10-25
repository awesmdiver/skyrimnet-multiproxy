#pragma once
// The few pieces of the SKSE plugin interface this mod needs, written out here so the mod links
// against no game library at all (no CommonLibSSE-NG, no Address Library).
//
// Source of every name, value and field order below: the comments in SKSE64's own public
// PluginAPI.h (https://github.com/ianpatt/skse64, skse64/PluginAPI.h, read 2026-10-25), plus its
// 2.1.2 / 2.2.0 release notes. Nothing is copied from that file: the structs are re-typed from the
// documented layout, and no SKSE code is linked in. SKSE (c) Ian Patterson, Stephen Abel, Paul
// Connelly, Adam Rippon, Brendan Borthwick; credit is in THIRD-PARTY-NOTICES.md.
//
// Who calls what:
//   SKSE64 for Special Edition 1.5.97          -> SKSEPlugin_Query, then SKSEPlugin_Load
//   SKSE64 for Anniversary Edition 1.6.x / 1.7 -> reads the SKSEPlugin_Version data, then SKSEPlugin_Load
//   SKSEVR (Skyrim VR 1.4.15)                  -> SKSEPlugin_Query, then SKSEPlugin_Load

#include <cstddef>
#include <cstdint>

namespace skse_api {

// PluginInfo, filled in by SKSEPlugin_Query.
struct PluginInfo {
    enum { kInfoVersion = 1 };
    std::uint32_t infoVersion;
    const char*   name;
    std::uint32_t version;
};

// SKSEInterface: only the first four fields are ever read, and this mod reads none of them. The
// rest are declared so the layout matches what SKSE passes in.
struct SKSEInterface {
    std::uint32_t skseVersion;
    std::uint32_t runtimeVersion;
    std::uint32_t editorVersion;
    std::uint32_t isEditor;
    void*         (*queryInterface)(std::uint32_t id);
    std::uint32_t (*getPluginHandle)();
    std::uint32_t (*getReleaseIndex)();
    const PluginInfo* (*getPluginInfo)(const char* name);
};

// SKSEPluginVersionData, the export SKSE64 1.6+ reads instead of running SKSEPlugin_Query.
// Field order and sizes are the documented ones (sizeof == 0x350).
struct SKSEPluginVersionData {
    enum { kVersion = 1 };

    // versionIndependence
    enum {
        kVersionIndependent_AddressLibraryPostAE = 1u << 0,  // uses the post-AE Address Library (we do NOT)
        kVersionIndependent_Signatures           = 1u << 1,  // only signature matching, no fixed offsets
        kVersionIndependent_StructsPost629       = 1u << 2,  // uses the 1.6.629+ struct layout
    };
    // versionIndependenceEx
    enum {
        kVersionIndependentEx_NoStructUse   = 1u << 0,  // uses no game structs at all
        kVersionIndependentEx_AddressLibraryV5 = 1u << 1,
    };

    std::uint32_t dataVersion;
    std::uint32_t pluginVersion;
    char          name[256];
    char          author[256];
    char          supportEmail[252];
    std::uint32_t versionIndependenceEx;
    std::uint32_t versionIndependence;
    std::uint32_t compatibleVersions[16];  // zero-terminated list; empty because we are version independent
    std::uint32_t seVersionRequired;       // 0 = no minimum
};
static_assert(sizeof(SKSEPluginVersionData) == 0x350, "SKSEPluginVersionData must be 0x350 bytes");
static_assert(offsetof(SKSEPluginVersionData, versionIndependenceEx) == 0x304);
static_assert(offsetof(SKSEPluginVersionData, compatibleVersions) == 0x30C);

}  // namespace skse_api
