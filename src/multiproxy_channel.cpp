// Pure Win32 -- must NOT include CommonLibSSE / PCH headers. See multiproxy_channel.h.
#define WIN32_LEAN_AND_MEAN
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <winsock2.h>
#include <ws2tcpip.h>
#include <mswsock.h>
#include <windows.h>
#include <shlobj.h>
#include <exdisp.h>
#include <shldisp.h>
#include <servprov.h>
#include <oleauto.h>

#include <algorithm>
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <memory>
#include <mutex>
#include <string>
#include <string_view>
#include <thread>
#include <utility>
#include <vector>

#include "multiproxy_channel.h"

// shellapi.h renames ShellExecute to ShellExecuteW, but IShellDispatch2 declares it under the plain name.
#ifdef ShellExecute
#undef ShellExecute
#endif

namespace mp {

// ---- text helpers -----------------------------------------------------------------------------

static std::wstring Widen(const std::string& utf8)
{
    if (utf8.empty()) return {};
    const int n = MultiByteToWideChar(CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()), nullptr, 0);
    if (n <= 0) return {};
    std::wstring out(static_cast<size_t>(n), L'\0');
    MultiByteToWideChar(CP_UTF8, 0, utf8.data(), static_cast<int>(utf8.size()), out.data(), n);
    return out;
}

static std::string Narrow(const std::wstring& w)
{
    if (w.empty()) return {};
    const int n = WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), nullptr, 0, nullptr, nullptr);
    if (n <= 0) return {};
    std::string out(static_cast<size_t>(n), '\0');
    WideCharToMultiByte(CP_UTF8, 0, w.data(), static_cast<int>(w.size()), out.data(), n, nullptr, nullptr);
    return out;
}

static std::string JsonEscape(const std::string& s)
{
    std::string out;
    out.reserve(s.size() + 8);
    for (const unsigned char c : s) {
        switch (c) {
            case '"':  out += "\\\""; break;
            case '\\': out += "\\\\"; break;
            case '\n': out += "\\n"; break;
            case '\r': out += "\\r"; break;
            case '\t': out += "\\t"; break;
            default:
                if (c < 0x20) {
                    char buf[8];
                    std::snprintf(buf, sizeof(buf), "\\u%04x", c);
                    out += buf;
                } else {
                    out += static_cast<char>(c);
                }
        }
    }
    return out;
}

static void Say(LogFn log, int level, const std::string& text)
{
    if (log) log(level, text.c_str());
}

// ---- a very small JSON reader (the pointer file is the only thing it ever reads) ----------------

namespace {

struct Json {
    enum class Type { Null, Bool, Number, String, Array, Object } type = Type::Null;
    double number = 0;
    bool boolean = false;
    std::string text;
    std::vector<Json> items;
    std::vector<std::pair<std::string, Json>> members;

    const Json* Find(const char* key) const
    {
        if (type != Type::Object) return nullptr;
        for (const auto& m : members) {
            if (m.first == key) return &m.second;
        }
        return nullptr;
    }
};

class JsonReader {
public:
    explicit JsonReader(const std::string& src) : s_(src) {}

    bool Parse(Json& out, std::string& err)
    {
        SkipSpace();
        if (!Value(out, 0)) { err = err_.empty() ? "not valid JSON" : err_; return false; }
        SkipSpace();
        if (i_ != s_.size()) { err = "extra text after the JSON value"; return false; }
        return true;
    }

private:
    const std::string& s_;
    size_t i_ = 0;
    std::string err_;

    void SkipSpace()
    {
        while (i_ < s_.size() && (s_[i_] == ' ' || s_[i_] == '\t' || s_[i_] == '\r' || s_[i_] == '\n')) ++i_;
    }

    bool Fail(const char* why) { err_ = why; return false; }

    bool Literal(const char* word)
    {
        const size_t n = std::char_traits<char>::length(word);
        if (s_.compare(i_, n, word) != 0) return false;
        i_ += n;
        return true;
    }

    static void AppendUtf8(std::string& out, unsigned long cp)
    {
        if (cp < 0x80) {
            out += static_cast<char>(cp);
        } else if (cp < 0x800) {
            out += static_cast<char>(0xC0 | (cp >> 6));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        } else if (cp < 0x10000) {
            out += static_cast<char>(0xE0 | (cp >> 12));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        } else {
            out += static_cast<char>(0xF0 | (cp >> 18));
            out += static_cast<char>(0x80 | ((cp >> 12) & 0x3F));
            out += static_cast<char>(0x80 | ((cp >> 6) & 0x3F));
            out += static_cast<char>(0x80 | (cp & 0x3F));
        }
    }

    bool Hex4(unsigned long& v)
    {
        if (i_ + 4 > s_.size()) return false;
        v = 0;
        for (int k = 0; k < 4; ++k) {
            const char c = s_[i_ + static_cast<size_t>(k)];
            v <<= 4;
            if (c >= '0' && c <= '9') v |= static_cast<unsigned long>(c - '0');
            else if (c >= 'a' && c <= 'f') v |= static_cast<unsigned long>(c - 'a' + 10);
            else if (c >= 'A' && c <= 'F') v |= static_cast<unsigned long>(c - 'A' + 10);
            else return false;
        }
        i_ += 4;
        return true;
    }

    bool String(std::string& out)
    {
        if (i_ >= s_.size() || s_[i_] != '"') return Fail("expected a string");
        ++i_;
        out.clear();
        while (i_ < s_.size()) {
            const char c = s_[i_++];
            if (c == '"') return true;
            if (c != '\\') { out += c; continue; }
            if (i_ >= s_.size()) break;
            const char e = s_[i_++];
            switch (e) {
                case '"': out += '"'; break;
                case '\\': out += '\\'; break;
                case '/': out += '/'; break;
                case 'b': out += '\b'; break;
                case 'f': out += '\f'; break;
                case 'n': out += '\n'; break;
                case 'r': out += '\r'; break;
                case 't': out += '\t'; break;
                case 'u': {
                    unsigned long cp = 0;
                    if (!Hex4(cp)) return Fail("bad \\u escape");
                    if (cp >= 0xD800 && cp <= 0xDBFF && i_ + 1 < s_.size() && s_[i_] == '\\' && s_[i_ + 1] == 'u') {
                        i_ += 2;
                        unsigned long lo = 0;
                        if (!Hex4(lo)) return Fail("bad \\u escape");
                        cp = 0x10000 + ((cp - 0xD800) << 10) + (lo - 0xDC00);
                    }
                    AppendUtf8(out, cp);
                    break;
                }
                default: return Fail("bad escape in a string");
            }
        }
        return Fail("a string never ends");
    }

    bool Value(Json& out, int depth)
    {
        if (depth > 16) return Fail("nested too deeply");
        SkipSpace();
        if (i_ >= s_.size()) return Fail("ended early");
        const char c = s_[i_];
        if (c == '{') {
            ++i_;
            out.type = Json::Type::Object;
            SkipSpace();
            if (i_ < s_.size() && s_[i_] == '}') { ++i_; return true; }
            for (;;) {
                SkipSpace();
                std::string key;
                if (!String(key)) return false;
                SkipSpace();
                if (i_ >= s_.size() || s_[i_] != ':') return Fail("expected ':'");
                ++i_;
                Json v;
                if (!Value(v, depth + 1)) return false;
                out.members.emplace_back(std::move(key), std::move(v));
                SkipSpace();
                if (i_ < s_.size() && s_[i_] == ',') { ++i_; continue; }
                if (i_ < s_.size() && s_[i_] == '}') { ++i_; return true; }
                return Fail("expected ',' or '}'");
            }
        }
        if (c == '[') {
            ++i_;
            out.type = Json::Type::Array;
            SkipSpace();
            if (i_ < s_.size() && s_[i_] == ']') { ++i_; return true; }
            for (;;) {
                Json v;
                if (!Value(v, depth + 1)) return false;
                out.items.push_back(std::move(v));
                SkipSpace();
                if (i_ < s_.size() && s_[i_] == ',') { ++i_; continue; }
                if (i_ < s_.size() && s_[i_] == ']') { ++i_; return true; }
                return Fail("expected ',' or ']'");
            }
        }
        if (c == '"') { out.type = Json::Type::String; return String(out.text); }
        if (Literal("true")) { out.type = Json::Type::Bool; out.boolean = true; return true; }
        if (Literal("false")) { out.type = Json::Type::Bool; out.boolean = false; return true; }
        if (Literal("null")) { out.type = Json::Type::Null; return true; }
        // a number
        const size_t start = i_;
        while (i_ < s_.size() && (std::string_view("+-0123456789.eE").find(s_[i_]) != std::string_view::npos)) ++i_;
        if (i_ == start) return Fail("unexpected character");
        out.type = Json::Type::Number;
        out.number = std::strtod(s_.substr(start, i_ - start).c_str(), nullptr);
        return true;
    }
};

}  // namespace

// ---- the pointer file -------------------------------------------------------------------------

std::wstring InstallInfoPath()
{
    wchar_t env[1024] = {};
    const DWORD n = GetEnvironmentVariableW(L"MULTIPROXY_INSTALL_INFO", env, static_cast<DWORD>(std::size(env)));
    if (n > 0 && n < std::size(env)) return env;

    wchar_t appdata[1024] = {};
    const DWORD m = GetEnvironmentVariableW(L"APPDATA", appdata, static_cast<DWORD>(std::size(appdata)));
    if (m == 0 || m >= std::size(appdata)) return {};
    return std::wstring(appdata) + L"\\SkyrimNet MultiProxy\\install-info.json";
}

ReadResult ReadInstallInfo(const std::wstring& path, InstallInfo& out, std::string& whyNot)
{
    if (path.empty()) { whyNot = "no %APPDATA% to look in"; return ReadResult::Missing; }

    HANDLE f = CreateFileW(path.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                           nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (f == INVALID_HANDLE_VALUE) {
        const DWORD err = GetLastError();
        if (err == ERROR_FILE_NOT_FOUND || err == ERROR_PATH_NOT_FOUND) { whyNot = "file not found"; return ReadResult::Missing; }
        whyNot = "could not open it (Windows error " + std::to_string(err) + ")";
        return ReadResult::Unreadable;
    }
    std::string bytes;
    char buf[4096];
    DWORD got = 0;
    while (ReadFile(f, buf, sizeof(buf), &got, nullptr) && got > 0) {
        bytes.append(buf, got);
        if (bytes.size() > (1u << 20)) break;  // a pointer file is a few hundred bytes
    }
    CloseHandle(f);
    if (bytes.size() >= 3 && static_cast<unsigned char>(bytes[0]) == 0xEF && static_cast<unsigned char>(bytes[1]) == 0xBB &&
        static_cast<unsigned char>(bytes[2]) == 0xBF) {
        bytes.erase(0, 3);
    }

    Json root;
    std::string err;
    if (!JsonReader(bytes).Parse(root, err) || root.type != Json::Type::Object) {
        whyNot = "not valid JSON (" + (err.empty() ? std::string("not an object") : err) + ")";
        return ReadResult::Unreadable;
    }

    InstallInfo info;
    if (const Json* p = root.Find("port"); p && p->type == Json::Type::Number) info.port = static_cast<int>(p->number);
    if (const Json* t = root.Find("token"); t && t->type == Json::Type::String) info.token = t->text;
    if (const Json* tray = root.Find("tray"); tray && tray->type == Json::Type::Object) {
        if (const Json* c = tray->Find("command"); c && c->type == Json::Type::Array) {
            for (const Json& item : c->items) {
                if (item.type == Json::Type::String) info.trayCommand.push_back(Widen(item.text));
            }
        }
        if (const Json* w = tray->Find("workingDirectory"); w && w->type == Json::Type::String) info.workingDirectory = Widen(w->text);
        if (const Json* a = tray->Find("fromSkyrimArg"); a && a->type == Json::Type::String) info.fromSkyrimArg = Widen(a->text);
    }
    if (info.port <= 0 || info.port > 65535) { whyNot = "no usable port"; return ReadResult::Unreadable; }
    if (info.token.empty()) { whyNot = "no token"; return ReadResult::Unreadable; }
    if (info.trayCommand.empty()) { whyNot = "no tray.command"; return ReadResult::Unreadable; }
    out = std::move(info);
    return ReadResult::Ok;
}

// ---- the HTTP call ----------------------------------------------------------------------------

static void EnsureWinsock()
{
    // Started once and never cleaned up on purpose: "closed" is sent while the process is exiting,
    // where starting Winsock fresh is not safe.
    static std::once_flag once;
    std::call_once(once, [] {
        WSADATA wsa{};
        WSAStartup(MAKEWORD(2, 2), &wsa);
    });
}

bool HttpCall(const InstallInfo& info, const char* method, const char* path, const std::string& body,
              int timeoutMs, int* status, std::string* replyBody)
{
    if (status) *status = 0;
    EnsureWinsock();

    SOCKET s = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (s == INVALID_SOCKET) return false;

    // Non-blocking connect bounded by select() -- see proxy_launcher.cpp's IsPortListening for why
    // a plain blocking connect to a refused loopback port is not reliably short.
    u_long nonBlocking = 1;
    ioctlsocket(s, FIONBIO, &nonBlocking);
    sockaddr_in addr{};
    addr.sin_family = AF_INET;
    addr.sin_port = htons(static_cast<u_short>(info.port));
    inet_pton(AF_INET, "127.0.0.1", &addr.sin_addr);
    connect(s, reinterpret_cast<sockaddr*>(&addr), sizeof(addr));

    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeoutMs);
    auto remainingMs = [&]() -> int {
        const auto left = std::chrono::duration_cast<std::chrono::milliseconds>(deadline - std::chrono::steady_clock::now()).count();
        return left > 0 ? static_cast<int>(left) : 0;
    };
    auto timevalFor = [](int ms) { return timeval{ms / 1000, (ms % 1000) * 1000}; };

    fd_set writeSet, exceptSet;
    FD_ZERO(&writeSet);
    FD_SET(s, &writeSet);
    FD_ZERO(&exceptSet);
    FD_SET(s, &exceptSet);
    timeval tv = timevalFor(remainingMs());
    const bool connected = select(0, nullptr, &writeSet, &exceptSet, &tv) > 0 && FD_ISSET(s, &writeSet) && !FD_ISSET(s, &exceptSet);
    if (!connected) { closesocket(s); return false; }

    nonBlocking = 0;
    ioctlsocket(s, FIONBIO, &nonBlocking);

    std::string req;
    req.reserve(256 + body.size());
    req += method;
    req += ' ';
    req += path;
    req += " HTTP/1.1\r\nHost: 127.0.0.1:";
    req += std::to_string(info.port);
    req += "\r\nAuthorization: Bearer ";
    req += info.token;
    req += "\r\nContent-Type: application/json\r\nContent-Length: ";
    req += std::to_string(body.size());
    req += "\r\nConnection: close\r\n\r\n";
    req += body;

    size_t sent = 0;
    while (sent < req.size()) {
        DWORD sendTimeout = static_cast<DWORD>(std::max(remainingMs(), 200));
        setsockopt(s, SOL_SOCKET, SO_SNDTIMEO, reinterpret_cast<const char*>(&sendTimeout), sizeof(sendTimeout));
        const int n = send(s, req.data() + sent, static_cast<int>(req.size() - sent), 0);
        if (n <= 0) { closesocket(s); return false; }
        sent += static_cast<size_t>(n);
    }

    std::string reply;
    char buf[2048];
    for (;;) {
        const int ms = remainingMs();
        if (ms <= 0) break;
        DWORD recvTimeout = static_cast<DWORD>(ms);
        setsockopt(s, SOL_SOCKET, SO_RCVTIMEO, reinterpret_cast<const char*>(&recvTimeout), sizeof(recvTimeout));
        const int n = recv(s, buf, sizeof(buf), 0);
        if (n <= 0) break;  // closed by the server (Connection: close) or timed out
        reply.append(buf, static_cast<size_t>(n));
        if (reply.size() > (64u << 10)) break;
    }
    closesocket(s);

    // "HTTP/1.1 200 OK"
    if (reply.size() < 12 || reply.compare(0, 5, "HTTP/") != 0) return false;
    const size_t sp = reply.find(' ');
    if (sp == std::string::npos) return false;
    const int code = std::atoi(reply.c_str() + sp + 1);
    if (code <= 0) return false;
    if (status) *status = code;
    if (replyBody) {
        const size_t split = reply.find("\r\n\r\n");
        *replyBody = split == std::string::npos ? std::string() : reply.substr(split + 4);
    }
    return true;
}

bool Ping(const InstallInfo& info, int timeoutMs)
{
    int status = 0;
    return HttpCall(info, "GET", "/local/ping", std::string(), timeoutMs, &status) && status == 200;
}

bool WaitForPing(const InstallInfo& info, int timeoutMs)
{
    const auto deadline = std::chrono::steady_clock::now() + std::chrono::milliseconds(timeoutMs);
    for (;;) {
        if (Ping(info, 1500)) return true;
        if (std::chrono::steady_clock::now() >= deadline) return false;
        std::this_thread::sleep_for(std::chrono::milliseconds(1000));
    }
}

// ---- starting the tray app outside the mod manager's job -----------------------------------------

namespace {

std::wstring QuoteArg(const std::wstring& a)
{
    return L"\"" + a + L"\"";
}

// The arguments after the program itself, as one string: the rest of tray.command, then the flag.
std::wstring TrayArguments(const InstallInfo& info)
{
    std::wstring args;
    for (size_t i = 1; i < info.trayCommand.size(); ++i) {
        if (!args.empty()) args += L' ';
        args += QuoteArg(info.trayCommand[i]);
    }
    if (!info.fromSkyrimArg.empty()) {
        if (!args.empty()) args += L' ';
        args += info.fromSkyrimArg;
    }
    return args;
}

// Raymond Chen's "run from Explorer": ask the desktop's own shell to start the program, so the new
// process is Explorer's child and belongs to no job of ours. Used when the job forbids breakaway.
bool StartViaExplorer(const std::wstring& file, const std::wstring& args, const std::wstring& dir, LogFn log)
{
    const HRESULT initHr = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    const bool weInitialised = SUCCEEDED(initHr);
    bool ok = false;

    IShellWindows* windows = nullptr;
    IDispatch* desktopDispatch = nullptr;
    IServiceProvider* provider = nullptr;
    IShellBrowser* browser = nullptr;
    IShellView* view = nullptr;
    IDispatch* background = nullptr;
    IShellFolderViewDual* folderView = nullptr;
    IDispatch* appDispatch = nullptr;
    IShellDispatch2* shell = nullptr;

    do {
        if (FAILED(CoCreateInstance(CLSID_ShellWindows, nullptr, CLSCTX_LOCAL_SERVER, IID_PPV_ARGS(&windows)))) {
            Say(log, 1, "Explorer's window list is not available");
            break;
        }
        VARIANT location;
        VariantInit(&location);
        location.vt = VT_I4;
        location.lVal = CSIDL_DESKTOP;
        VARIANT empty;
        VariantInit(&empty);
        long hwnd = 0;
        if (FAILED(windows->FindWindowSW(&location, &empty, SWC_DESKTOP, &hwnd, SWFO_NEEDDISPATCH, &desktopDispatch)) || !desktopDispatch) {
            Say(log, 1, "Explorer's desktop window was not found");
            break;
        }
        if (FAILED(desktopDispatch->QueryInterface(IID_PPV_ARGS(&provider)))) break;
        if (FAILED(provider->QueryService(SID_STopLevelBrowser, IID_PPV_ARGS(&browser)))) break;
        if (FAILED(browser->QueryActiveShellView(&view))) break;
        if (FAILED(view->GetItemObject(SVGIO_BACKGROUND, IID_PPV_ARGS(&background)))) break;
        if (FAILED(background->QueryInterface(IID_PPV_ARGS(&folderView)))) break;
        if (FAILED(folderView->get_Application(&appDispatch))) break;
        if (FAILED(appDispatch->QueryInterface(IID_PPV_ARGS(&shell)))) break;

        BSTR bFile = SysAllocString(file.c_str());
        VARIANT vArgs, vDir, vOp, vShow;
        VariantInit(&vArgs); VariantInit(&vDir); VariantInit(&vOp); VariantInit(&vShow);
        vArgs.vt = VT_BSTR; vArgs.bstrVal = SysAllocString(args.c_str());
        vDir.vt = VT_BSTR;  vDir.bstrVal = SysAllocString(dir.c_str());
        vOp.vt = VT_BSTR;   vOp.bstrVal = SysAllocString(L"open");
        vShow.vt = VT_I4;   vShow.lVal = SW_HIDE;
        const HRESULT hr = shell->ShellExecute(bFile, vArgs, vDir, vOp, vShow);
        SysFreeString(bFile);
        VariantClear(&vArgs); VariantClear(&vDir); VariantClear(&vOp);
        ok = SUCCEEDED(hr);
        if (!ok) Say(log, 1, "Explorer refused to start it (HRESULT 0x" + [&] { char b[16]; std::snprintf(b, sizeof(b), "%08lx", static_cast<unsigned long>(hr)); return std::string(b); }() + ")");
    } while (false);

    if (shell) shell->Release();
    if (appDispatch) appDispatch->Release();
    if (folderView) folderView->Release();
    if (background) background->Release();
    if (view) view->Release();
    if (browser) browser->Release();
    if (provider) provider->Release();
    if (desktopDispatch) desktopDispatch->Release();
    if (windows) windows->Release();
    if (weInitialised) CoUninitialize();
    return ok;
}

bool CreateTrayProcess(const std::wstring& file, const std::wstring& args, const std::wstring& dir, DWORD extraFlags, DWORD* error)
{
    std::wstring cmd = QuoteArg(file);
    if (!args.empty()) cmd += L" " + args;
    std::vector<wchar_t> buf(cmd.begin(), cmd.end());
    buf.push_back(L'\0');

    STARTUPINFOW si{};
    si.cb = sizeof(si);
    si.dwFlags = STARTF_USESHOWWINDOW;
    si.wShowWindow = SW_HIDE;
    PROCESS_INFORMATION pi{};
    const BOOL ok = CreateProcessW(file.c_str(), buf.data(), nullptr, nullptr, FALSE,
                                   DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW | extraFlags, nullptr,
                                   dir.empty() ? nullptr : dir.c_str(), &si, &pi);
    if (!ok) {
        if (error) *error = GetLastError();
        return false;
    }
    CloseHandle(pi.hProcess);
    CloseHandle(pi.hThread);
    return true;
}

}  // namespace

TrayStart StartTrayOutsideJob(const InstallInfo& info, LogFn log)
{
    if (info.trayCommand.empty()) return TrayStart::Failed;
    const std::wstring& file = info.trayCommand[0];
    const std::wstring args = TrayArguments(info);
    const std::wstring& dir = info.workingDirectory;

    BOOL inJob = FALSE;
    IsProcessInJob(GetCurrentProcess(), nullptr, &inJob);
    Say(log, 0, std::string("Starting the MultiProxy tray app (Skyrim ") + (inJob ? "is" : "is not") + " running inside a job object)");

    DWORD err = 0;
    if (CreateTrayProcess(file, args, dir, CREATE_BREAKAWAY_FROM_JOB, &err)) return TrayStart::BrokeAway;
    Say(log, 1, "Starting it outside the job failed (Windows error " + std::to_string(err) + "); asking Explorer to start it instead");

    if (StartViaExplorer(file, args, dir, log)) return TrayStart::ViaShell;

    // Last resort. Inside the job it still runs, but a mod manager that waits for everything in
    // its job may stay locked after the game closes -- "Start and stop with Skyrim" quits it then.
    if (CreateTrayProcess(file, args, dir, 0, &err)) {
        Say(log, 1, "Could not start it outside the job; it is running inside it. Mod Organizer 2 may stay locked after Skyrim closes until the tray app quits.");
        return TrayStart::InsideJob;
    }
    Say(log, 2, "Could not start the tray app at all (Windows error " + std::to_string(err) + "): " + Narrow(file));
    return TrayStart::Failed;
}

// ---- telling it Skyrim started / closed ------------------------------------------------------------

bool PostStarted(const InstallInfo& info, const std::wstring& gameFolder, const char* modVersion, unsigned long pid)
{
    std::string folder = Narrow(gameFolder);
    while (folder.size() > 3 && (folder.back() == '\\' || folder.back() == '/')) folder.pop_back();
    const std::string body = "{\"folder\":\"" + JsonEscape(folder) + "\",\"modVersion\":\"" + JsonEscape(modVersion ? modVersion : "") +
                             "\",\"pid\":" + std::to_string(pid) + "}";
    int status = 0;
    return HttpCall(info, "POST", "/local/skyrim/started", body, 5000, &status) && status == 200;
}

bool PostClosed(const InstallInfo& info, int timeoutMs)
{
    int status = 0;
    return HttpCall(info, "POST", "/local/skyrim/closed", "{}", timeoutMs, &status) && status == 200;
}

// ---- the exit notice ---------------------------------------------------------------------------------
//
// Skyrim gives SKSE no "the game is closing" message, so the notice rides on the DLL being
// detached while the process ends. That runs under the loader lock with every other thread already
// gone, so it does the least possible: one loopback request over Winsock that is already started.
// It cannot help when the process is ended by TerminateProcess (a crash, End Task): nothing in the
// process runs then. The tray app can double-check with the pid sent in "started".

namespace {
std::mutex g_armMutex;
InstallInfo* g_armed = nullptr;       // leaked on purpose: used during process exit
std::atomic<bool> g_closedSent{false};
}  // namespace

// Windows detaches DLLs in reverse load order, and Winsock's base provider (mswsock.dll) is loaded
// by ws2_32 on the first socket -- after this DLL -- so by the time this DLL's DllMain runs at exit
// it is already torn down and every socket call fails. (Measured: "closed" never arrived at
// ExitProcess; with Winsock started before the DLL loaded, it did.) Importing mswsock.dll
// statically makes the loader map it BEFORE this DLL, so it is torn down AFTER it.
#pragma comment(lib, "mswsock.lib")
#ifdef _WIN64
#pragma comment(linker, "/INCLUDE:__imp_TransmitFile")
#endif
static volatile const void* g_keepMswsockImported = nullptr;

void ArmExitNotice(const InstallInfo& info)
{
    g_keepMswsockImported = reinterpret_cast<const void*>(&TransmitFile);  // a real reference, so the import survives /OPT:REF
    std::lock_guard<std::mutex> lock(g_armMutex);
    if (!g_armed) g_armed = new InstallInfo(info);
    else *g_armed = info;
}

static void SendClosedAtProcessExit()
{
    // No locks here (loader lock held, other threads gone): read the pointer, nothing else.
    InstallInfo* armed = g_armed;
    if (!armed) return;
    if (g_closedSent.exchange(true)) return;
    PostClosed(*armed, 1500);
}

// ---- the whole start-up sequence ---------------------------------------------------------------------

void RunStartup(const InstallInfo& info, const std::wstring& gameFolder, const char* modVersion, LogFn log)
{
    const char* version = modVersion ? modVersion : "";

    if (Ping(info)) {
        Say(log, 0, "MultiProxy is already running");
    } else {
        Say(log, 0, "MultiProxy is not answering; starting the tray app");
        const TrayStart how = StartTrayOutsideJob(info, log);
        switch (how) {
            case TrayStart::BrokeAway: Say(log, 0, "Tray app started outside the job"); break;
            case TrayStart::ViaShell: Say(log, 0, "Tray app started through Explorer"); break;
            case TrayStart::InsideJob: break;  // already said so
            case TrayStart::Failed: return;
        }
        // The server loads its models and checks its AIs before it answers.
        if (!WaitForPing(info, 60 * 1000)) {
            Say(log, 2, "MultiProxy did not answer within 60 seconds of starting the tray app. Look at multiproxy-tray.log in the MultiProxy settings folder.");
            return;
        }
        Say(log, 0, "MultiProxy answered");
    }

    for (int attempt = 1; attempt <= 3; ++attempt) {
        if (PostStarted(info, gameFolder, version, GetCurrentProcessId())) {
            ArmExitNotice(info);
            Say(log, 0, "Told MultiProxy that Skyrim started");
            return;
        }
        std::this_thread::sleep_for(std::chrono::milliseconds(1500));
    }
    Say(log, 2, "MultiProxy answered a ping but would not take 'Skyrim started' (a changed token? run the MultiProxy installer again)");
}

}  // namespace mp

// Sends "closed" when the game process ends normally (ExitProcess). lpReserved is non-null exactly
// when the process is terminating, as opposed to this DLL being unloaded on its own.
BOOL APIENTRY DllMain(HMODULE, DWORD reason, LPVOID reserved)
{
    if (reason == DLL_PROCESS_DETACH && reserved != nullptr) {
        mp::SendClosedAtProcessExit();
    }
    return TRUE;
}
