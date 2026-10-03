// Mimics a mod manager that runs the game inside a job object (Mod Organizer 2 does) and then
// waits for the job to empty before it unlocks its window. See README.md.
//
//   job_box.exe <breakaway|silent|none|nojob> <probe.exe> ["probe arguments"]
//
// breakaway : job allows CREATE_BREAKAWAY_FROM_JOB      (what a breakaway-friendly manager sets)
// silent    : job breaks children away on its own        (JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK)
// none      : job allows no breakaway at all             (forces the "ask Explorer" fallback)
// nojob     : no job (what Vortex does when it runs a tool)
//
// All job modes also set KILL_ON_JOB_CLOSE, so anything still inside when this program ends dies --
// exactly why a tray app left inside the job would be a problem.
#include <windows.h>

#include <chrono>
#include <cstdio>
#include <cwchar>
#include <string>
#include <thread>

#include "multiproxy_channel.h"

int wmain(int argc, wchar_t** argv)
{
    if (argc < 3) {
        std::printf("usage: job_box <breakaway|silent|none|nojob> <probe.exe>\n");
        return 2;
    }
    const std::wstring mode = argv[1];
    HANDLE job = nullptr;
    if (mode != L"nojob") {
        job = CreateJobObjectW(nullptr, nullptr);
        JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        if (mode == L"breakaway") limits.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_BREAKAWAY_OK;
        else if (mode == L"silent") limits.BasicLimitInformation.LimitFlags |= JOB_OBJECT_LIMIT_SILENT_BREAKAWAY_OK;
        else if (mode != L"none") { std::printf("unknown mode\n"); return 2; }
        SetInformationJobObject(job, JobObjectExtendedLimitInformation, &limits, sizeof(limits));
    }

    std::wstring cmd = L"\"" + std::wstring(argv[2]) + L"\"";
    if (argc > 3) cmd += L" " + std::wstring(argv[3]);  // optional arguments for the probe (the negative control uses this)
    std::wstring buf = cmd;
    STARTUPINFOW si{};
    si.cb = sizeof(si);
    PROCESS_INFORMATION pi{};
    if (!CreateProcessW(nullptr, buf.data(), nullptr, nullptr, TRUE, CREATE_SUSPENDED | CREATE_BREAKAWAY_FROM_JOB, nullptr, nullptr, &si, &pi) &&
        !CreateProcessW(nullptr, buf.data(), nullptr, nullptr, TRUE, CREATE_SUSPENDED, nullptr, nullptr, &si, &pi)) {
        std::printf("could not start the probe (error %lu)\n", GetLastError());
        return 2;
    }
    if (job && !AssignProcessToJobObject(job, pi.hProcess)) {
        std::printf("could not put the probe in the job (error %lu)\n", GetLastError());
        TerminateProcess(pi.hProcess, 1);
        return 2;
    }
    BOOL probeInJob = FALSE;
    IsProcessInJob(pi.hProcess, job, &probeInJob);
    std::printf("[box] mode=%ls, probe in the job: %s\n", mode.c_str(), probeInJob ? "yes" : "no");
    ResumeThread(pi.hThread);

    const DWORD waited = WaitForSingleObject(pi.hProcess, 180 * 1000);
    DWORD code = 0;
    GetExitCodeProcess(pi.hProcess, &code);
    std::printf("[box] probe exited (%s, code %lu)\n", waited == WAIT_OBJECT_0 ? "on its own" : "TIMED OUT", code);
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);

    bool unlocked = true;
    if (job) {
        // What the mod manager does: wait for everything in the job to finish. Give it 10 s.
        const auto start = std::chrono::steady_clock::now();
        DWORD active = 0;
        for (;;) {
            JOBOBJECT_BASIC_ACCOUNTING_INFORMATION acct{};
            QueryInformationJobObject(job, JobObjectBasicAccountingInformation, &acct, sizeof(acct), nullptr);
            active = acct.ActiveProcesses;
            if (active == 0) break;
            if (std::chrono::steady_clock::now() - start > std::chrono::seconds(10)) break;
            std::this_thread::sleep_for(std::chrono::milliseconds(100));
        }
        const auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(std::chrono::steady_clock::now() - start).count();
        unlocked = active == 0;
        std::printf("[box] job after the game closed: %lu process(es) still inside after %lld ms -> mod manager %s\n", active,
                    static_cast<long long>(ms), unlocked ? "UNLOCKS" : "STAYS LOCKED");
    } else {
        std::printf("[box] no job: nothing for a mod manager to wait on\n");
    }

    // Is MultiProxy still up? (the tray app must keep running after the game is gone)
    mp::InstallInfo info;
    std::string why;
    if (mp::ReadInstallInfo(mp::InstallInfoPath(), info, why) == mp::ReadResult::Ok) {
        std::printf("[box] MultiProxy answers a ping after the game closed: %s\n", mp::Ping(info, 3000) ? "yes" : "NO");
    }
    if (job) CloseHandle(job);  // KILL_ON_JOB_CLOSE: anything left inside dies here
    return unlocked ? 0 : 1;
}
