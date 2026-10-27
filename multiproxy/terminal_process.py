"""Run a program inside a pseudo-terminal, with nothing on screen (2026-10-06). Standard library only: `pty` on Linux, Windows' own ConPTY through `ctypes`.

Why: Anthropic's `claude setup-token` draws its sign-in screen only when it is on a real terminal (with plain pipes it prints nothing and waits), so the Courier
has to give it one that nobody can see. The Courier reads what the program prints, and types the one-time code the player pasted on the main device.

    term = open_terminal([exe, "setup-token"], env, cwd)      # raises TerminalUnavailable when this device can't make one (the caller says so in one plain line)
    text = term.read()      # what arrived since the last call (bytes, ANSI and all; never waits)
    term.write("code\\r")    # what a person would type, then Enter
    term.alive(); term.returncode; term.kill()

The terminal is made very wide (`COLUMNS`) so a long link or code is never broken over two lines. Everything the program starts goes with it when `kill()` is called
(a job object on Windows, a process group on Linux). Nothing here ever logs or keeps what the program prints.
"""
from __future__ import annotations

import os
import queue
import sys
import threading
import time
from typing import Optional

import hidden_process

COLUMNS = 500
ROWS = 40


class TerminalUnavailable(OSError):
    """This device can't give the program a pseudo-terminal (an old Windows without ConPTY, no `pty` module, ...)."""


class _Base:
    def __init__(self) -> None:
        self._chunks: "queue.Queue[bytes]" = queue.Queue()
        self._eof = threading.Event()
        self.returncode: Optional[int] = None
        self._gone_at: Optional[float] = None
        self._killed = False

    def read(self) -> bytes:
        out = []
        while True:
            try:
                out.append(self._chunks.get_nowait())
            except queue.Empty:
                break
        return b"".join(out)

    def ended(self) -> bool:
        """The program has gone AND everything it printed has been handed over. (On Windows the output pipe only ends when the terminal is closed, so a program that
        has gone and printed nothing for half a second counts as finished.)"""
        if self.alive():
            self._gone_at = None
            return False
        if self._gone_at is None:
            self._gone_at = time.monotonic()
        return self._chunks.empty() and (self._eof.is_set() or time.monotonic() - self._gone_at > 0.5)


# ------------------------------------------------------------------------------------------------------------------------------------------------
# Linux (and other POSIX)
# ------------------------------------------------------------------------------------------------------------------------------------------------
class _PosixTerminal(_Base):
    def __init__(self, argv: list, env: dict, cwd: Optional[str]) -> None:
        super().__init__()
        import fcntl
        import pty
        import struct
        import termios
        try:
            master, slave = pty.openpty()
        except OSError as e:
            raise TerminalUnavailable(str(e))
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack("HHHH", ROWS, COLUMNS, 0, 0))
        env = {**env, "TERM": env.get("TERM") or "xterm-256color", "COLUMNS": str(COLUMNS), "LINES": str(ROWS)}
        try:
            self._proc = hidden_process.popen(argv, stdin=slave, stdout=slave, stderr=slave, env=env, cwd=cwd, start_new_session=True, close_fds=True)
        except OSError:
            os.close(master)
            raise
        finally:
            os.close(slave)
        self._master = master
        self._reader = threading.Thread(target=self._pump, name="terminal-reader", daemon=True)
        self._reader.start()

    def _pump(self) -> None:
        while True:
            try:
                data = os.read(self._master, 65536)
            except OSError:
                break
            if not data:
                break
            self._chunks.put(data)
        self._eof.set()

    def write(self, text: str) -> None:
        os.write(self._master, text.encode("utf-8"))

    def alive(self) -> bool:
        rc = self._proc.poll()
        if rc is not None:
            self.returncode = rc
        return rc is None

    def kill(self) -> None:
        import signal
        if self._killed:
            return                           # once only: the program's number may belong to another program by now, and a second kill would end THAT one
        self._killed = True
        try:
            os.killpg(self._proc.pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            try:
                self._proc.kill()
            except OSError:
                pass
        try:
            self._proc.wait(timeout=5)
        except Exception:
            pass
        self.alive()
        self._reader.join(3)                 # the reader ends by itself once the program is gone; the descriptor is closed only after that (a number closed under a
        try:                                 # blocked read can be handed to the next terminal, and the old reader would then steal its output)
            os.close(self._master)
        except OSError:
            pass
        self._eof.set()


# ------------------------------------------------------------------------------------------------------------------------------------------------
# Windows: ConPTY
# ------------------------------------------------------------------------------------------------------------------------------------------------
if sys.platform == "win32":
    import ctypes
    from ctypes import wintypes as wt

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    HANDLE = ctypes.c_void_p
    HPCON = ctypes.c_void_p
    EXTENDED_STARTUPINFO_PRESENT = 0x00080000
    CREATE_UNICODE_ENVIRONMENT = 0x00000400
    PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE = 0x00020016
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    JobObjectExtendedLimitInformation = 9
    STILL_ACTIVE = 259
    STARTF_USESTDHANDLES = 0x00000100

    class COORD(ctypes.Structure):
        _fields_ = [("X", ctypes.c_short), ("Y", ctypes.c_short)]

    class STARTUPINFOW(ctypes.Structure):
        _fields_ = [("cb", wt.DWORD), ("lpReserved", wt.LPWSTR), ("lpDesktop", wt.LPWSTR), ("lpTitle", wt.LPWSTR), ("dwX", wt.DWORD), ("dwY", wt.DWORD),
                    ("dwXSize", wt.DWORD), ("dwYSize", wt.DWORD), ("dwXCountChars", wt.DWORD), ("dwYCountChars", wt.DWORD), ("dwFillAttribute", wt.DWORD),
                    ("dwFlags", wt.DWORD), ("wShowWindow", wt.WORD), ("cbReserved2", wt.WORD), ("lpReserved2", ctypes.c_void_p), ("hStdInput", HANDLE),
                    ("hStdOutput", HANDLE), ("hStdError", HANDLE)]

    class STARTUPINFOEXW(ctypes.Structure):
        _fields_ = [("StartupInfo", STARTUPINFOW), ("lpAttributeList", ctypes.c_void_p)]

    class PROCESS_INFORMATION(ctypes.Structure):
        _fields_ = [("hProcess", HANDLE), ("hThread", HANDLE), ("dwProcessId", wt.DWORD), ("dwThreadId", wt.DWORD)]

    class IO_COUNTERS(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ("ReadOperationCount", "WriteOperationCount", "OtherOperationCount", "ReadTransferCount", "WriteTransferCount", "OtherTransferCount")]

    class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("PerProcessUserTimeLimit", ctypes.c_longlong), ("PerJobUserTimeLimit", ctypes.c_longlong), ("LimitFlags", wt.DWORD), ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t), ("ActiveProcessLimit", wt.DWORD), ("Affinity", ctypes.c_size_t), ("PriorityClass", wt.DWORD), ("SchedulingClass", wt.DWORD)]

    class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION), ("IoInfo", IO_COUNTERS), ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t), ("PeakProcessMemoryUsed", ctypes.c_size_t), ("PeakJobMemoryUsed", ctypes.c_size_t)]

    def _declare() -> None:
        _k32.CreatePipe.argtypes = [ctypes.POINTER(HANDLE), ctypes.POINTER(HANDLE), ctypes.c_void_p, wt.DWORD]
        _k32.CreatePipe.restype = wt.BOOL
        _k32.CreatePseudoConsole.argtypes = [COORD, HANDLE, HANDLE, wt.DWORD, ctypes.POINTER(HPCON)]
        _k32.CreatePseudoConsole.restype = ctypes.c_long
        _k32.ClosePseudoConsole.argtypes = [HPCON]
        _k32.ClosePseudoConsole.restype = None
        _k32.InitializeProcThreadAttributeList.argtypes = [ctypes.c_void_p, wt.DWORD, wt.DWORD, ctypes.POINTER(ctypes.c_size_t)]
        _k32.InitializeProcThreadAttributeList.restype = wt.BOOL
        _k32.UpdateProcThreadAttribute.argtypes = [ctypes.c_void_p, wt.DWORD, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_size_t, ctypes.c_void_p, ctypes.c_void_p]
        _k32.UpdateProcThreadAttribute.restype = wt.BOOL
        _k32.CreateProcessW.argtypes = [wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p, ctypes.c_void_p, wt.BOOL, wt.DWORD, ctypes.c_void_p, wt.LPCWSTR, ctypes.c_void_p,
                                        ctypes.POINTER(PROCESS_INFORMATION)]
        _k32.CreateProcessW.restype = wt.BOOL
        _k32.ReadFile.argtypes = [HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
        _k32.ReadFile.restype = wt.BOOL
        _k32.WriteFile.argtypes = [HANDLE, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD), ctypes.c_void_p]
        _k32.WriteFile.restype = wt.BOOL
        _k32.CloseHandle.argtypes = [HANDLE]
        _k32.CloseHandle.restype = wt.BOOL
        _k32.GetExitCodeProcess.argtypes = [HANDLE, ctypes.POINTER(wt.DWORD)]
        _k32.GetExitCodeProcess.restype = wt.BOOL
        _k32.TerminateProcess.argtypes = [HANDLE, wt.UINT]
        _k32.TerminateProcess.restype = wt.BOOL
        _k32.CreateJobObjectW.argtypes = [ctypes.c_void_p, wt.LPCWSTR]
        _k32.CreateJobObjectW.restype = HANDLE
        _k32.SetInformationJobObject.argtypes = [HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD]
        _k32.SetInformationJobObject.restype = wt.BOOL
        _k32.AssignProcessToJobObject.argtypes = [HANDLE, HANDLE]
        _k32.AssignProcessToJobObject.restype = wt.BOOL

    _declare()

    def _env_block(env: dict) -> "ctypes.Array":
        text = "".join(f"{k}={v}\0" for k, v in sorted(env.items(), key=lambda kv: kv[0].upper())) + "\0"
        return ctypes.create_unicode_buffer(text, len(text))

    class _WindowsTerminal(_Base):
        def __init__(self, argv: list, env: dict, cwd: Optional[str]) -> None:
            super().__init__()
            if not hasattr(_k32, "CreatePseudoConsole"):
                raise TerminalUnavailable("This version of Windows doesn't have the terminal feature needed to run this.")
            self._hpc = HPCON()
            self._in_w, self._out_r = HANDLE(), HANDLE()
            self._process: Optional[HANDLE] = None
            self._job: Optional[HANDLE] = None
            in_r, out_w = HANDLE(), HANDLE()
            if not _k32.CreatePipe(ctypes.byref(in_r), ctypes.byref(self._in_w), None, 0) or not _k32.CreatePipe(ctypes.byref(self._out_r), ctypes.byref(out_w), None, 0):
                raise TerminalUnavailable("Couldn't make the terminal's pipes.")
            hr = _k32.CreatePseudoConsole(COORD(COLUMNS, ROWS), in_r, out_w, 0, ctypes.byref(self._hpc))
            _k32.CloseHandle(in_r)
            _k32.CloseHandle(out_w)
            if hr != 0:
                self._close_pipes()
                raise TerminalUnavailable(f"Couldn't open the terminal (error {hr & 0xFFFFFFFF:#x}).")
            try:
                self._start(argv, env, cwd)
            except BaseException:
                _k32.ClosePseudoConsole(self._hpc)
                self._close_pipes()
                raise
            threading.Thread(target=self._pump, name="terminal-reader", daemon=True).start()

        def _close_pipes(self) -> None:
            for h in (self._in_w, self._out_r):
                if h:
                    _k32.CloseHandle(h)
            self._in_w, self._out_r = HANDLE(), HANDLE()

        def _start(self, argv: list, env: dict, cwd: Optional[str]) -> None:
            import subprocess as _sp          # only for its quoting rules (`list2cmdline`), never to start anything
            size = ctypes.c_size_t(0)
            _k32.InitializeProcThreadAttributeList(None, 1, 0, ctypes.byref(size))
            attrs = ctypes.create_string_buffer(size.value)
            if not _k32.InitializeProcThreadAttributeList(attrs, 1, 0, ctypes.byref(size)):
                raise TerminalUnavailable("Couldn't prepare the program's start-up.")
            if not _k32.UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_PSEUDOCONSOLE, self._hpc, ctypes.sizeof(HPCON), None, None):
                raise TerminalUnavailable("Couldn't attach the pseudo-terminal.")
            si = STARTUPINFOEXW()
            si.StartupInfo.cb = ctypes.sizeof(si)
            si.lpAttributeList = ctypes.cast(attrs, ctypes.c_void_p)
            si.StartupInfo.dwFlags = STARTF_USESTDHANDLES       # with no handles named: the program gets the pseudo-terminal's, never this process's own (which may be a pipe)
            pi = PROCESS_INFORMATION()
            cmd = ctypes.create_unicode_buffer(_sp.list2cmdline([str(a) for a in argv]))
            env = {**env, "TERM": env.get("TERM") or "xterm-256color", "COLUMNS": str(COLUMNS), "LINES": str(ROWS)}
            block = _env_block(env)
            if not _k32.CreateProcessW(None, cmd, None, None, False, EXTENDED_STARTUPINFO_PRESENT | CREATE_UNICODE_ENVIRONMENT, block, cwd, ctypes.byref(si), ctypes.byref(pi)):
                raise OSError(ctypes.get_last_error(), "MultiProxy couldn't start the program.")
            _k32.CloseHandle(pi.hThread)
            self._process = HANDLE(pi.hProcess)
            # Everything the program starts dies with it: a job object that closes with us.
            job = _k32.CreateJobObjectW(None, None)
            if job:
                info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
                info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
                if _k32.SetInformationJobObject(job, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)):
                    _k32.AssignProcessToJobObject(job, self._process)
                    self._job = HANDLE(job)
                else:
                    _k32.CloseHandle(job)

        def _pump(self) -> None:
            buf = ctypes.create_string_buffer(65536)
            got = wt.DWORD(0)
            while True:
                handle = self._out_r
                if not handle or not _k32.ReadFile(handle, buf, 65536, ctypes.byref(got), None) or got.value == 0:
                    break
                self._chunks.put(buf.raw[:got.value])
            self._eof.set()

        def write(self, text: str) -> None:
            data = text.encode("utf-8")
            written = wt.DWORD(0)
            if not self._in_w or not _k32.WriteFile(self._in_w, data, len(data), ctypes.byref(written), None):
                raise OSError("The terminal is closed.")

        def alive(self) -> bool:
            if self._process is None:
                return False
            code = wt.DWORD(0)
            if not _k32.GetExitCodeProcess(self._process, ctypes.byref(code)):
                return False
            if code.value == STILL_ACTIVE:
                return True
            self.returncode = int(code.value)
            return False

        def kill(self) -> None:
            if self._process is not None:
                _k32.TerminateProcess(self._process, 1)
            if self._job:
                _k32.CloseHandle(self._job)             # the job closing ends anything the program started
                self._job = None
            if self._hpc:
                _k32.ClosePseudoConsole(self._hpc)      # the reader's pipe ends, so its thread finishes
                self._hpc = HPCON()
            deadline = time.monotonic() + 3
            while not self._eof.is_set() and time.monotonic() < deadline:
                time.sleep(0.02)
            self.alive()
            self._close_pipes()
            if self._process is not None:
                _k32.CloseHandle(self._process)
                self._process = None
            self._eof.set()


def open_terminal(argv: list, env: dict, cwd: Optional[str] = None) -> _Base:
    """Start `argv` on a hidden pseudo-terminal. Raises TerminalUnavailable when this device can't, OSError when the program can't be started."""
    if sys.platform == "win32":
        return _WindowsTerminal(argv, env, cwd)
    try:
        import pty  # noqa: F401
        import termios  # noqa: F401
    except ImportError as e:
        raise TerminalUnavailable(str(e))
    return _PosixTerminal(argv, env, cwd)
