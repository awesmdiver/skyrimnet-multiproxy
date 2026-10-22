#pragma once

// Checks that the Address Library file CommonLibSSE-NG will want is there, so the mod can step
// aside politely instead of letting CommonLibSSE-NG kill the game ("failed to open address
// library file", REL/Relocation.h). Pure standard C++ on purpose: the unit test in
// tools/address-library-test compiles this header on its own.
//
// The naming rule is copied from CommonLibSSE-NG's REL::IDDatabase::load (v3.7.0), which formats
// REL::Version::string() with its default separator "-" (so the parts are joined by hyphens):
//   Skyrim SE (1.5.x):  Data/SKSE/Plugins/version-<major>-<minor>-<revision>-<build>.bin
//   Skyrim AE (1.6.x+): Data/SKSE/Plugins/versionlib-<major>-<minor>-<revision>-<build>.bin
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

    inline std::string VersionJoined(const GameVersion& v, const char* separator)
    {
        return std::to_string(v.major) + separator + std::to_string(v.minor) + separator +
               std::to_string(v.revision) + separator + std::to_string(v.build);
    }

    // "1.6.1170.0": all four parts with dots, the readable form for the box and the log.
    inline std::string VersionText(const GameVersion& v) { return VersionJoined(v, "."); }

    // Anniversary Edition runtimes are 1.6 and up; everything older is Skyrim SE.
    inline bool IsAnniversaryEdition(const GameVersion& v)
    {
        return v.major > 1 || (v.major == 1 && v.minor >= 6);
    }

    inline std::string FileNameFor(const GameVersion& v)
    {
        return std::string(IsAnniversaryEdition(v) ? "versionlib-" : "version-") + VersionJoined(v, "-") + ".bin";
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

    // Which version decides whether MultiProxy may start. The first flag is for the version
    // CommonLibSSE-NG will read (REL::Module), the second for what SKSE reports. Stop only when the
    // file for the first is missing and the file for the second is missing too: a load that worked
    // before is never blocked.
    enum class Verdict { Start, StartOnSecondary, Stop };

    inline Verdict Decide(bool primaryPresent, bool secondaryPresent)
    {
        if (primaryPresent) return Verdict::Start;
        return secondaryPresent ? Verdict::StartOnSecondary : Verdict::Stop;
    }

    inline std::string BothNamesLogLine(const GameVersion& primary, const GameVersion& secondary)
    {
        return "The Address Library file " + FileNameFor(primary) + " (the version the game files report) was not found, "
               "but " + FileNameFor(secondary) + " (the version SKSE reports) is there, so MultiProxy is not stopping.";
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
