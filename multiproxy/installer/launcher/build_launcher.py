"""Builds the two small start programs (2026-10-26): `SkyrimNet MultiProxy.exe` and `MultiProxy Courier.exe`.

Windows names an item in Settings > Apps > Startup from the program file's own description. MultiProxy and the Courier used to be started at sign-in as the bundled
`pythonw.exe`, so they showed as "Python" (publisher "Python Software Foundation"). These programs have their own name, publisher and icon (a version resource) and do one
thing: start `python\\pythonw.exe <script>` with the arguments they were given, then exit (see launcher.c).

    python installer/launcher/build_launcher.py multiproxy|courier VERSION OUT.exe

Built with MSVC (cl.exe and rc.exe, the toolchain the Skyrim builds already use; no new tool). Found on PATH when the build runs from a developer prompt, else through
vswhere and vcvarsall.bat. Plain-language failure when it is not there.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ICONS = HERE.parent / "icons"

PRODUCTS = {
    "multiproxy": {"exe": "SkyrimNet MultiProxy.exe", "name": "SkyrimNet MultiProxy", "script": "multiproxy_tray.py", "icon": "multiproxy.ico"},
    "courier": {"exe": "MultiProxy Courier.exe", "name": "MultiProxy Courier", "script": "courier.py", "icon": "courier.ico"},
}


class LauncherError(Exception):
    """A plain reason the start program can't be built."""


def msvc_env() -> dict:
    """The environment cl.exe and rc.exe need (PATH, INCLUDE, LIB): the current one when cl is already on PATH with INCLUDE set, else vcvarsall's."""
    if shutil.which("cl") and os.environ.get("INCLUDE") and shutil.which("rc"):
        return dict(os.environ)
    pf = os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")
    vswhere = Path(pf) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
    if vswhere.is_file():
        found = subprocess.run([str(vswhere), "-latest", "-products", "*", "-property", "installationPath"], capture_output=True, text=True)
        root = (found.stdout or "").strip()
        vcvars = Path(root) / "VC" / "Auxiliary" / "Build" / "vcvarsall.bat" if root else None
        if vcvars and vcvars.is_file():
            out = subprocess.run(f'cmd /c ""{vcvars}" x64 >nul && set"', capture_output=True, text=True, shell=True)
            env = {}
            for line in (out.stdout or "").splitlines():
                if "=" in line:
                    k, v = line.split("=", 1)
                    env[k] = v
            if env.get("INCLUDE"):
                return env
    raise LauncherError("The start programs are built with Microsoft's C++ build tools (cl.exe and rc.exe, the ones the Skyrim builds use), and they weren't found. "
                        "Open a Visual Studio developer prompt and run the build again.")


def version_parts(version: str) -> tuple:
    nums = [int(x) for x in re.findall(r"\d+", version)[:4]] + [0, 0, 0, 0]
    return tuple(nums[:4])


def build(product: str, version: str, out: Path, work: Path = None) -> Path:
    spec = PRODUCTS[product]
    out = Path(out)
    work = Path(work) if work else HERE.parent / ".cache" / "launcher" / product
    work.mkdir(parents=True, exist_ok=True)
    env = msvc_env()
    rc_text = (HERE / "launcher.rc.in").read_text(encoding="utf-8")
    for key, val in {"@ICON@": str(ICONS / spec["icon"]).replace("\\", "\\\\"), "@VERSION_COMMA@": ",".join(str(n) for n in version_parts(version)), "@VERSION@": version,
                     "@NAME@": spec["name"], "@STEM@": Path(spec["exe"]).stem, "@FILENAME@": spec["exe"]}.items():
        rc_text = rc_text.replace(key, val)
    rc_file, res_file, obj_exe = work / "launcher.rc", work / "launcher.res", work / spec["exe"]
    rc_file.write_text(rc_text, encoding="utf-8")
    run = lambda cmd: subprocess.run(cmd, cwd=str(work), env=env, capture_output=True, text=True)
    r = run(["rc", "/nologo", "/fo", str(res_file), str(rc_file)])
    if r.returncode != 0:
        raise LauncherError(f"rc.exe couldn't build the version resource of {spec['exe']}:\n{(r.stdout or '') + (r.stderr or '')}")
    r = run(["cl", "/nologo", "/O1", "/MT", "/GS", "/W3", "/DUNICODE", "/D_UNICODE", f'/DSCRIPT_NAME=L"{spec["script"]}"', f"/Fo{work}\\launcher.obj", f"/Fe{obj_exe}",
             str(HERE / "launcher.c"), str(res_file), "/link", "/SUBSYSTEM:WINDOWS", "/ENTRY:wWinMainCRTStartup", "user32.lib"])
    if r.returncode != 0 or not obj_exe.is_file():
        raise LauncherError(f"cl.exe couldn't build {spec['exe']}:\n{(r.stdout or '') + (r.stderr or '')}")
    out.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(obj_exe, out)
    return out


if __name__ == "__main__":
    if len(sys.argv) != 4 or sys.argv[1] not in PRODUCTS:
        print(__doc__)
        sys.exit(2)
    try:
        print(build(sys.argv[1], sys.argv[2], Path(sys.argv[3])))
    except LauncherError as e:
        print(f"\nThe start program wasn't built: {e}", file=sys.stderr)
        sys.exit(1)
