#pragma once

// Checks that the Address Library file CommonLibSSE-NG will want is there, so the mod can step
// aside politely instead of letting CommonLibSSE-NG kill the game ("failed to open address
// library file", REL/Relocation.h). Pure standard C++ on purpose: the unit test in
// tools/address-library-test compiles this header on its own.
//
// The naming rule is copied from CommonLibSSE-NG's REL::IDDatabase::load (v3.7.0):
//   Skyrim SE (1.5.x):  Data/SKSE/Plugins/version-<major>.<minor>.<revision>.<build>.bin
//   Skyrim AE (1.6.x+): Data/SKSE/Plugins/versionlib-<major>.<minor>.<revision>.<build>.bin
// CommonLibSSE-NG opens the path relative to the game folder; checking <game folder>\Data\SKSE\Plugins
// is the same file, and goes through the same file calls Mod Organizer 2 / Vortex's virtual folder hooks.

#include <cstdint>
#include <filesystem>
#include <fstream>
#include <string>

namespace addrlib
{
    struct GameVersion
    {
        std::uint16_t major{};
        std::uint16_t minor{};
        std::uint16_t revision{};
        std::uint16_t build{};
    };

    // "1.6.1170.0": all four parts, the way CommonLibSSE-NG writes the version into the file name.
    inline std::string VersionText(const GameVersion& v)
    {
        return std::to_string(v.major) + "." + std::to_string(v.minor) + "." +
               std::to_string(v.revision) + "." + std::to_string(v.build);
    }

    // Anniversary Edition runtimes are 1.6 and up; everything older is Skyrim SE.
    inline bool IsAnniversaryEdition(const GameVersion& v)
    {
        return v.major > 1 || (v.major == 1 && v.minor >= 6);
    }

    inline std::string FileNameFor(const GameVersion& v)
    {
        return std::string(IsAnniversaryEdition(v) ? "versionlib-" : "version-") + VersionText(v) + ".bin";
    }

    // True when the file exists and can be opened for reading.
    inline bool FileIsReadable(const std::filesystem::path& file)
    {
        std::ifstream in(file, std::ios::in | std::ios::binary);
        return in.is_open();
    }

    inline bool IsPresent(const std::filesystem::path& pluginsDir, const GameVersion& v)
    {
        return FileIsReadable(pluginsDir / FileNameFor(v));
    }

    // The two sentences the player and the log see. The wording is the design side's.
    inline std::string MessageBoxText(const GameVersion& v)
    {
        return "MultiProxy can't start because the Address Library for your Skyrim version (" + VersionText(v) +
               ") is missing. Install the matching \"Address Library for SKSE Plugins\" from Nexus Mods, then start the game again. "
               "The game will run without MultiProxy until then.";
    }

    inline std::string LogLine(const GameVersion& v)
    {
        return "The Address Library file " + FileNameFor(v) + " was not found, so MultiProxy did not start. "
               "Install Address Library for SKSE Plugins for Skyrim " + VersionText(v) + ".";
    }
}
