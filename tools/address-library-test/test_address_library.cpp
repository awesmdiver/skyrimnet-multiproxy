// Unit test for src/address_library_check.h (no game, no CommonLibSSE-NG). Build + run: build.ps1
#include "address_library_check.h"

#include <cstdio>
#include <cstdlib>

static int failures = 0;
#define CHECK(cond)                                                              \
    do {                                                                         \
        if (!(cond)) { std::printf("FAIL line %d: %s\n", __LINE__, #cond); ++failures; } \
    } while (0)

int main()
{
    const addrlib::GameVersion se{1, 5, 97, 0};
    const addrlib::GameVersion ae{1, 6, 1170, 0};
    const addrlib::GameVersion newer{1, 6, 1179, 0};

    CHECK(addrlib::FileNameFor(se) == "version-1-5-97-0.bin");
    CHECK(addrlib::FileNameFor(ae) == "versionlib-1-6-1170-0.bin");
    CHECK(addrlib::FileNameFor(newer) == "versionlib-1-6-1179-0.bin");
    CHECK(addrlib::VersionText(ae) == "1.6.1170.0");
    CHECK(!addrlib::IsAnniversaryEdition(se));
    CHECK(addrlib::IsAnniversaryEdition(ae));

    namespace fs = std::filesystem;
    const fs::path dir = fs::temp_directory_path() / "address-library-test";
    fs::remove_all(dir);
    fs::create_directories(dir);

    // The exact file the player has installed, named the way the real Address Library names it.
    const addrlib::GameVersion swapped{1, 6, 1130, 0};
    const addrlib::GameVersion future{1, 7, 104, 0};
    CHECK(addrlib::FileNameFor(swapped) == "versionlib-1-6-1130-0.bin");
    CHECK(addrlib::FileNameFor(future) == "versionlib-1-7-104-0.bin");
    { std::ofstream(dir / "versionlib-1-6-1130-0.bin", std::ios::binary) << "x"; }
    CHECK(addrlib::IsPresent(dir, swapped));
    fs::remove(dir / "versionlib-1-6-1130-0.bin");
    CHECK(!addrlib::IsPresent(dir, swapped));
    CHECK(addrlib::MessageBoxText(swapped).find("(1.6.1130.0)") != std::string::npos);

    // Two-source rule: stop only when neither version's file is there.
    CHECK(addrlib::Decide(true, true) == addrlib::Verdict::Start);
    CHECK(addrlib::Decide(true, false) == addrlib::Verdict::Start);
    CHECK(addrlib::Decide(false, true) == addrlib::Verdict::StartOnSecondary);
    CHECK(addrlib::Decide(false, false) == addrlib::Verdict::Stop);

    // Missing: nothing there yet.
    CHECK(!addrlib::IsPresent(dir, se));
    CHECK(!addrlib::IsPresent(dir, ae));
    CHECK(!addrlib::IsPresent(dir / "no-such-folder", ae));

    // Present: the file for exactly that version.
    { std::ofstream(dir / addrlib::FileNameFor(ae), std::ios::binary) << "x"; }
    CHECK(addrlib::IsPresent(dir, ae));
    CHECK(!addrlib::IsPresent(dir, se));
    // A newer game than the Address Library on disk is just "that file is not there".
    CHECK(!addrlib::IsPresent(dir, newer));

    // Unreadable: a folder where the file should be cannot be opened as a file.
    fs::create_directories(dir / addrlib::FileNameFor(se));
    CHECK(!addrlib::IsPresent(dir, se));

    CHECK(addrlib::LogLine(ae) ==
          "The Address Library file versionlib-1-6-1170-0.bin was not found, so MultiProxy did not start. "
          "Install Address Library for SKSE Plugins for Skyrim 1.6.1170.0.");

    fs::remove_all(dir);
    if (failures == 0) std::printf("All address library checks passed.\n");
    return failures == 0 ? 0 : 1;
}
