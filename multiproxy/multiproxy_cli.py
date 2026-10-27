"""What the Linux `multiproxy` command asks this program (2026-10-05). Standard library only, so it answers in a blink.

    python multiproxy_cli.py status      one plain line: running (with how many AIs are ready) or not running; exit code 0 when running
    python multiproxy_cli.py port        the port this copy answers on
    python multiproxy_cli.py url         the dashboard's address
    python multiproxy_cli.py open        open the dashboard in the browser (prints the address when there is no desktop to open it in)
    python multiproxy_cli.py stop        ask a running server to shut down cleanly (it stops its local AI engines first); exit code 0 when it has gone
    python multiproxy_cli.py pointer     write the small pointer file the Skyrim mod and the tray app read (the installer runs this once)

The server's own settings folder, port and token are found the same way the server and the tray app find them (`settings_files`, `local_channel`),
so this never disagrees with them. Nothing here prints a key or the token.
"""
import json
import os
import re
import socket
import sys
import time
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path

PROGRAM_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(PROGRAM_DIR))
import settings_files  # noqa: E402  (after the path line: the program folder is not on the path when this runs as a script from elsewhere)

SETTINGS_DIR = Path(settings_files.settings_dir(PROGRAM_DIR))


def read_version() -> str:
    try:
        m = re.search(r'^PROXY_VERSION\s*=\s*"([^"]+)"', (PROGRAM_DIR / "multiproxy.py").read_text(encoding="utf-8"), re.M)
        return m.group(1) if m else ""
    except OSError:
        return ""


def port() -> int:
    """MULTIPROXY_PORT, else the Settings page's choice (config.json), else 8000: the order the server uses."""
    env = os.environ.get("MULTIPROXY_PORT", "").strip()
    if env.isdigit() and 0 < int(env) < 65536:
        return int(env)
    p = settings_files.read_json(str(SETTINGS_DIR / "config.json")).get("main_port")
    return int(p) if isinstance(p, int) and 1024 <= p <= 65535 else 8000


def url() -> str:
    return f"http://127.0.0.1:{port()}"


def _token() -> str:
    try:
        import local_channel
        return str(local_channel.read_info().get("token") or "")
    except Exception:
        return ""


def _get(path: str, timeout: float = 3.0, token: str = ""):
    req = urllib.request.Request(url() + path, headers={"Authorization": f"Bearer {token}"} if token else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except (urllib.error.URLError, OSError, ValueError, socket.timeout):
        return None


def status_line() -> tuple:
    """(exit code, the line). Running means the server answers on its port."""
    health = _get("/health")
    if health is None:
        return 1, "MultiProxy is not running."
    st = _get("/local/tray-status", token=_token())
    version = (st or {}).get("version") or read_version()
    head = f"MultiProxy {version}".rstrip()
    if st and st.get("totalAIs") is not None:
        ready, total = int(st.get("readyCount") or 0), int(st.get("totalAIs") or 0)
        return 0, f"{head} is running. {ready} of {total} AIs ready."
    return 0, f"{head} is running."


def open_dashboard(opener=webbrowser.open) -> int:
    where = url()
    has_desktop = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY") or sys.platform != "linux")
    if has_desktop and opener(where):
        return 0
    print(f"Open this in a browser: {where}")
    return 0


def stop_server(wait: float = 20.0) -> int:
    """A clean stop through the local channel. 0 when the server is gone afterwards (or wasn't running), 1 when it didn't go in time."""
    if _get("/health", timeout=1.5) is None:
        return 0
    req = urllib.request.Request(url() + "/local/shutdown", method="POST", data=b"{}",
                                 headers={"Authorization": f"Bearer {_token()}", "Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=5).read()
    except (urllib.error.URLError, OSError, socket.timeout):
        pass
    deadline = time.time() + wait
    while time.time() < deadline:
        if _get("/health", timeout=1.0) is None:
            return 0
        time.sleep(0.4)
    return 1


def write_pointer() -> int:
    """The pointer file (the Skyrim mod and the tray app read it; it also turns the local channel on). The tray command is this program's own Python
    and its tray app: the same command the Windows install writes for pythonw."""
    import local_channel
    command = settings_files.tray_start_command(str(PROGRAM_DIR), sys.executable)
    local_channel.write_info(port=port(), command=command, program_folder=str(PROGRAM_DIR), settings_folder=str(SETTINGS_DIR))
    return 0


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args[0] if args else "status"
    if cmd == "status":
        code, line = status_line()
        print(line)
        return code
    if cmd == "port":
        print(port())
        return 0
    if cmd == "url":
        print(url())
        return 0
    if cmd == "open":
        return open_dashboard()
    if cmd == "stop":
        return stop_server()
    if cmd == "pointer":
        return write_pointer()
    print("Usage: multiproxy_cli.py status|port|url|open|stop|pointer", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
