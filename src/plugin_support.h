#pragma once
// Plain Windows helpers for the SKSE plugin: which Skyrim this is, where its SKSE log folder is,
// the game's version, and a small thread-safe log writer. Header-only so tools/plugin-test links
// the REAL code. Include <windows.h> (and <winsock2.h> before it, if used) first.

#include <cstdio>
#include <ctime>
#include <share.h>
#include <filesystem>
#include <mutex>
#include <string>
#include <vector>

#include <shlobj.h>
#include <winver.h>
#pragma comment(lib, "version.lib")
#pragma comment(lib, "shell32.lib")
#pragma comment(lib, "ole32.lib")

namespace plug {

namespace fs = std::filesystem;

enum class Edition { SpecialEdition, VR };

// SkyrimVR.exe is VR; every other name (SkyrimSE.exe, and anything unexpected) is SE/AE.
inline Edition EditionFromExePath(const std::wstring& exePath)
{
    std::wstring name = fs::path(exePath).filename().wstring();
    for (auto& c : name) c = static_cast<wchar_t>(::towlower(c));
    return name == L"skyrimvr.exe" ? Edition::VR : Edition::SpecialEdition;
}

inline const char* EditionLabel(Edition e) { return e == Edition::VR ? "Skyrim VR" : "Skyrim Special Edition / Anniversary Edition"; }

// The folder names under Documents\My Games\ that this edition's SKSE writes its logs to. SE has
// three because the Steam, GOG and Epic builds each use their own.
inline std::vector<std::wstring> DocumentsFolderNames(Edition e)
{
    if (e == Edition::VR) return { L"Skyrim VR" };
    return { L"Skyrim Special Edition", L"Skyrim Special Edition GOG", L"Skyrim Special Edition EPIC" };
}

// <Documents>\My Games\<edition>\SKSE. SKSE itself creates that folder before it loads plugins, so
// the first candidate that already exists is the right store; when none does, the first name.
inline fs::path ChooseLogFolder(const fs::path& documents, Edition e)
{
    const auto names = DocumentsFolderNames(e);
    for (const auto& n : names) {
        std::error_code ec;
        const fs::path p = documents / L"My Games" / n / L"SKSE";
        if (fs::is_directory(p, ec)) return p;
    }
    return documents / L"My Games" / names.front() / L"SKSE";
}

struct LogTarget {
    fs::path path;
    bool     usedFallback = false;  // true: the Documents folder could not be used, the log sits beside the DLL
};

// Picks where SkyrimNetMultiProxy.log goes. documents may be empty (could not be found).
// The caller proves the choice by opening the file; if that fails it calls this with
// forceFallback = true.
inline LogTarget ResolveLogTarget(const fs::path& documents, Edition e, const fs::path& dllFolder,
                                  bool forceFallback = false)
{
    LogTarget t;
    if (!forceFallback && !documents.empty()) {
        const fs::path dir = ChooseLogFolder(documents, e);
        std::error_code ec;
        fs::create_directories(dir, ec);
        if (fs::is_directory(dir, ec)) {
            t.path = dir / L"SkyrimNetMultiProxy.log";
            return t;
        }
    }
    t.path = dllFolder / L"SkyrimNetMultiProxy.log";
    t.usedFallback = true;
    return t;
}

inline fs::path DocumentsFolder()
{
    PWSTR docs = nullptr;
    fs::path result;
    if (SUCCEEDED(::SHGetKnownFolderPath(FOLDERID_Documents, 0, nullptr, &docs)) && docs) result = docs;
    if (docs) ::CoTaskMemFree(docs);
    return result;
}

inline std::wstring ModulePathOf(HMODULE m)
{
    wchar_t buf[MAX_PATH * 2] = {};
    const DWORD n = ::GetModuleFileNameW(m, buf, static_cast<DWORD>(std::size(buf)));
    return std::wstring(buf, n);
}

// Folder this DLL sits in (pass any address inside the DLL).
inline fs::path DllFolder(const void* addressInsideDll)
{
    HMODULE h = nullptr;
    if (::GetModuleHandleExW(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS | GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                             static_cast<LPCWSTR>(addressInsideDll), &h)) {
        return fs::path(ModulePathOf(h)).parent_path();
    }
    return fs::path(ModulePathOf(nullptr)).parent_path();
}

// "1.4.15.0" from the file version of an .exe; empty when it has none.
inline std::string ReadFileVersion(const std::wstring& path)
{
    DWORD ignored = 0;
    const DWORD size = ::GetFileVersionInfoSizeW(path.c_str(), &ignored);
    if (size == 0) return {};
    std::vector<char> data(size);
    if (!::GetFileVersionInfoW(path.c_str(), 0, size, data.data())) return {};
    VS_FIXEDFILEINFO* info = nullptr;
    UINT len = 0;
    if (!::VerQueryValueW(data.data(), L"\\", reinterpret_cast<LPVOID*>(&info), &len) || !info || len < sizeof(*info)) return {};
    char buf[64];
    std::snprintf(buf, sizeof(buf), "%u.%u.%u.%u", HIWORD(info->dwFileVersionMS), LOWORD(info->dwFileVersionMS),
                  HIWORD(info->dwFileVersionLS), LOWORD(info->dwFileVersionLS));
    return buf;
}

// One log file, one writer lock, flushed after every line (same behaviour as the spdlog setup it
// replaces), line format "[2026-10-25 14:03:09] [info] text".
class Logger {
public:
    bool Open(const fs::path& path)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (file_) { std::fclose(file_); file_ = nullptr; }
        // Shared open, so the log can be read (or tailed) while the game is running.
        file_ = ::_wfsopen(path.c_str(), L"wb", _SH_DENYNO);
        return file_ != nullptr;
    }
    void Write(const char* level, const std::string& text)
    {
        std::lock_guard<std::mutex> lock(mutex_);
        if (!file_) return;
        std::time_t now = std::time(nullptr);
        std::tm tm{};
        ::localtime_s(&tm, &now);
        char stamp[32];
        std::strftime(stamp, sizeof(stamp), "%Y-%m-%d %H:%M:%S", &tm);
        std::fprintf(file_, "[%s] [%s] %s\n", stamp, level, text.c_str());
        std::fflush(file_);
    }
private:
    std::mutex mutex_;
    std::FILE* file_ = nullptr;
};

}  // namespace plug
