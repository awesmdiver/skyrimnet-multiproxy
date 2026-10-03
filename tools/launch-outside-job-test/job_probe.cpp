// Stands in for Skyrim in the job-object test (see README.md): runs the mod's REAL start-up
// sequence (src/multiproxy_channel.cpp), prints what happened, and exits -- "the game closing".
// Prints the pid of the tray app's process tree root is not possible from here; job_box.exe checks
// the job instead.
#include <cstdio>
#include <string>

#include "multiproxy_channel.h"

static void Log(int level, const char* text)
{
    static const char* names[] = {"info", "warn", "error"};
    std::printf("[probe %s] %s\n", names[level < 0 || level > 2 ? 2 : level], text);
    std::fflush(stdout);
}

int wmain()
{
    mp::InstallInfo info;
    std::string why;
    const std::wstring path = mp::InstallInfoPath();
    if (mp::ReadInstallInfo(path, info, why) != mp::ReadResult::Ok) {
        std::printf("[probe error] pointer file not usable: %s\n", why.c_str());
        return 2;
    }
    mp::RunStartup(info, L"C:\\Fake Skyrim\\", "test", Log);
    std::printf("[probe] game 'closing' now\n");
    std::fflush(stdout);
    return 0;
}
