/*
 * The small program Windows sees when it starts MultiProxy or the Courier at sign-in (2026-10-26).
 *
 * Windows names an item in Settings > Apps > Startup from the program file's own description, so a Run entry that points at the bundled pythonw.exe shows up as "Python".
 * This file is that program instead: it has its own name, publisher and icon (the version resource, launcher.rc.in), and it does ONE thing: start
 *
 *     <this folder>\python\pythonw.exe  "<this folder>\SCRIPT_NAME"  <whatever this program was given>
 *
 * with this folder as the working directory, and then exit. It never waits, so the process that really runs the tray app (or the Courier) is pythonw's own, and every
 * process number MultiProxy records (its own, its engines') is that one. No window, no network, no registry, no files written, nothing else.
 *
 * Built by installer\launcher\build_launcher.py with MSVC (the one that is already installed for the Skyrim builds); SCRIPT_NAME is set per product.
 */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <wchar.h>

#ifndef SCRIPT_NAME
#error SCRIPT_NAME (the .py file to start) is set by build_launcher.py
#endif

#define MAX_CMD 32768

/* Appends one argument, quoted the way CommandLineToArgvW reads it back (the paths here have spaces; quotes and backslashes are escaped properly). */
static int add_quoted(wchar_t *cmd, size_t cap, const wchar_t *arg)
{
    size_t n = wcslen(cmd);
    size_t i = 0;
    if (n + 3 >= cap)
        return 0;
    cmd[n++] = L'"';
    for (;;) {
        size_t slashes = 0;
        while (arg[i] == L'\\') { slashes++; i++; }
        if (arg[i] == L'\0') {                       /* backslashes before the closing quote are doubled */
            if (n + slashes * 2 + 3 >= cap) return 0;
            while (slashes--) { cmd[n++] = L'\\'; cmd[n++] = L'\\'; }
            break;
        }
        if (arg[i] == L'"') {                        /* backslashes before a quote are doubled, the quote is escaped */
            if (n + slashes * 2 + 3 >= cap) return 0;
            while (slashes--) { cmd[n++] = L'\\'; cmd[n++] = L'\\'; }
            cmd[n++] = L'\\'; cmd[n++] = L'"';
        } else {
            if (n + slashes + 2 >= cap) return 0;
            while (slashes--) cmd[n++] = L'\\';
            cmd[n++] = arg[i];
        }
        i++;
    }
    cmd[n++] = L'"';
    cmd[n] = L'\0';
    return 1;
}

/* What this program was given: wWinMain's command line already leaves out this program's own name, so it is the arguments as the caller wrote them (their quoting is kept
 * exactly); only the spaces in front are dropped. */
static const wchar_t *skip_spaces(const wchar_t *line)
{
    while (*line == L' ' || *line == L'	')
        line++;
    return line;
}

int WINAPI wWinMain(HINSTANCE inst, HINSTANCE prev, LPWSTR line, int show)
{
    static wchar_t self[MAX_PATH * 2], dir[MAX_PATH * 2], python[MAX_PATH * 2], script[MAX_PATH * 2], cmd[MAX_CMD];
    wchar_t *slash;
    STARTUPINFOW si;
    PROCESS_INFORMATION pi;
    const wchar_t *tail;
    DWORD len;
    (void)inst; (void)prev; (void)show;

    len = GetModuleFileNameW(NULL, self, (DWORD)(sizeof(self) / sizeof(self[0])));
    if (len == 0 || len >= sizeof(self) / sizeof(self[0]))
        return 2;
    wcscpy_s(dir, sizeof(dir) / sizeof(dir[0]), self);
    slash = wcsrchr(dir, L'\\');
    if (!slash)
        return 2;
    *slash = L'\0';
    if (swprintf_s(python, sizeof(python) / sizeof(python[0]), L"%s\\python\\pythonw.exe", dir) < 0 ||
        swprintf_s(script, sizeof(script) / sizeof(script[0]), L"%s\\%s", dir, SCRIPT_NAME) < 0)
        return 2;

    cmd[0] = L'\0';
    if (!add_quoted(cmd, MAX_CMD, python) || wcslen(cmd) + 1 >= MAX_CMD)
        return 2;
    wcscat_s(cmd, MAX_CMD, L" ");
    if (!add_quoted(cmd, MAX_CMD, script))
        return 2;
    tail = skip_spaces(line ? line : L"");
    if (*tail) {
        if (wcslen(cmd) + 1 + wcslen(tail) >= MAX_CMD)
            return 2;
        wcscat_s(cmd, MAX_CMD, L" ");
        wcscat_s(cmd, MAX_CMD, tail);
    }

    ZeroMemory(&si, sizeof(si));
    si.cb = sizeof(si);
    ZeroMemory(&pi, sizeof(pi));
    if (!CreateProcessW(python, cmd, NULL, NULL, FALSE, 0, NULL, dir, &si, &pi))
        return 1;
    CloseHandle(pi.hThread);
    CloseHandle(pi.hProcess);
    return 0;
}
