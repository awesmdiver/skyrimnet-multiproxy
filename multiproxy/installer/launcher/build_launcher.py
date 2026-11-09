"""Builds the two small start programs (2026-10-26): `SkyrimNet MultiProxy.exe` and `MultiProxy Courier.exe`.

Windows names an item in Settings > Apps > Startup from the program file's own description. MultiProxy and the Courier used to be started at sign-in as the bundled
`pythonw.exe`, so they showed as "Python" (publisher "Python Software Foundation"). These programs have their own name, publisher and icon (a version resource) and do one
thing: start `python\\pythonw.exe <script>` with the arguments they were given, then exit (see launcher.c).

    python installer/launcher/build_launcher.py multiproxy|courier VERSION OUT.exe

Built with MSVC (cl.exe and rc.exe, the toolchain the Skyrim builds already use; no new tool). Found on PATH when the build runs from a developer prompt, else in the
Windows SDK folder or through vswhere and vcvarsall.bat (added to the PATH of this one build only). Plain-language failure when one is not there.
"""
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
ICONS = HERE.parent / "icons"

PRODUCTS = {
    "multiproxy": {"exe": "SkyrimNet MultiProxy.exe", "name": "SkyrimNet MultiProxy", "script": "multiproxy_tray.py", "icon": "multiproxy.ico"},
    "courier": {"exe": "MultiProxy Courier.exe", "name": "MultiProxy Courier", "script": "courier.py", "icon": "courier.ico"},
}


class LauncherError(Exception):
    """A plain reason the start program can't be built."""


def _program_files() -> list:
    return [p for p in (os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"), os.environ.get("ProgramFiles", r"C:\Program Files")) if p]


def _sdk_bin_dirs() -> list:
    """The Windows SDK's tool folders, newest version first: Windows Kits\\10\\bin\\<version>\\x64 (rc.exe lives there), then the unversioned x64 folder."""
    out = []
    for pf in _program_files():
        base = Path(pf) / "Windows Kits" / "10" / "bin"
        if not base.is_dir():
            continue
        versions = sorted((d for d in base.iterdir() if d.is_dir() and re.match(r"\d+(\.\d+)+$", d.name)), key=lambda d: [int(x) for x in d.name.split(".")], reverse=True)
        out += [v / "x64" for v in versions] + [base / "x64"]
    return [d for d in out if d.is_dir()]


def _vswhere_bin_dirs(name: str) -> list:
    """Where Visual Studio / the Build Tools keep `name` (cl.exe, link.exe), asked of vswhere.exe."""
    for pf in _program_files():
        vswhere = Path(pf) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
        if vswhere.is_file():
            try:
                found = subprocess.run([str(vswhere), "-latest", "-products", "*", "-find", f"VC\\Tools\\MSVC\\**\\Hostx64\\x64\\{name}.exe"], capture_output=True, text=True)
            except OSError:
                return []
            return [Path(line.strip()).parent for line in (found.stdout or "").splitlines() if line.strip()]
    return []


def find_tool(name: str, env: dict) -> Optional[Path]:
    """Full path of `name`.exe: on the PATH in `env`, else in the Windows SDK, else where vswhere says Visual Studio keeps it. None when it can't be found. (A bare name
    is not enough for subprocess: on Windows it looks on the PATH of this process, not the one in `env`.)"""
    hit = shutil.which(name, path=env.get("PATH"))
    if hit:
        return Path(hit)
    for d in _sdk_bin_dirs() + _vswhere_bin_dirs(name):
        if (d / f"{name}.exe").is_file():
            return d / f"{name}.exe"
    return None


def _vcvars_env() -> Optional[dict]:
    for pf in _program_files():
        vswhere = Path(pf) / "Microsoft Visual Studio" / "Installer" / "vswhere.exe"
        if not vswhere.is_file():
            continue
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
    return None


def _missing(tool: str) -> LauncherError:
    what = {"rc": "rc.exe was not found. It comes with the Windows SDK.", "cl": "cl.exe was not found. It comes with the Microsoft C++ build tools."}.get(tool, f"{tool}.exe was not found.")
    return LauncherError(f"{what} Run this from a Claude terminal, or install the Windows SDK and the C++ build tools (Visual Studio Build Tools), then run the build again.")


def toolchain() -> tuple:
    """(environment, {"rc": path, "cl": path}) for building the start programs from any prompt. The current environment when cl is already on PATH with INCLUDE set,
    else the one vcvarsall.bat makes; a tool still missing from that PATH is looked for in the usual places and its folder is added to the PATH of this build only (the
    person's own settings are never changed). Raises LauncherError naming the missing tool."""
    env = dict(os.environ)
    if not (shutil.which("cl") and env.get("INCLUDE")):
        env = _vcvars_env() or env
    tools = {}
    for t in ("rc", "cl"):
        path = find_tool(t, env)
        if path is None:
            raise _missing(t)
        tools[t] = str(path)
        if not shutil.which(t, path=env.get("PATH")):
            env["PATH"] = str(path.parent) + os.pathsep + env.get("PATH", "")
    if not env.get("INCLUDE"):
        raise LauncherError("The C++ build tools' include folders weren't found (cl.exe needs them). Run this from a Claude terminal, or from a Visual Studio developer prompt, then run the build again.")
    return env, tools


def msvc_env() -> dict:
    """The environment cl.exe and rc.exe need (PATH, INCLUDE, LIB); see toolchain()."""
    return toolchain()[0]


def version_parts(version: str) -> tuple:
    nums = [int(x) for x in re.findall(r"\d+", version)[:4]] + [0, 0, 0, 0]
    return tuple(nums[:4])


def build(product: str, version: str, out: Path, work: Path = None) -> Path:
    spec = PRODUCTS[product]
    out = Path(out)
    work = Path(work) if work else HERE.parent / ".cache" / "launcher" / product
    work.mkdir(parents=True, exist_ok=True)
    env, tools = toolchain()
    rc_text = (HERE / "launcher.rc.in").read_text(encoding="utf-8")
    for key, val in {"@ICON@": str(ICONS / spec["icon"]).replace("\\", "\\\\"), "@VERSION_COMMA@": ",".join(str(n) for n in version_parts(version)), "@VERSION@": version,
                     "@NAME@": spec["name"], "@STEM@": Path(spec["exe"]).stem, "@FILENAME@": spec["exe"]}.items():
        rc_text = rc_text.replace(key, val)
    rc_file, res_file, obj_exe = work / "launcher.rc", work / "launcher.res", work / spec["exe"]
    rc_file.write_text(rc_text, encoding="utf-8")
    def run(cmd):
        try:
            return subprocess.run(cmd, cwd=str(work), env=env, capture_output=True, text=True)
        except OSError as e:
            raise _missing(Path(cmd[0]).stem) if isinstance(e, FileNotFoundError) else LauncherError(f"{Path(cmd[0]).name} couldn't be started ({e.__class__.__name__}).")
    r = run([tools["rc"], "/nologo", "/fo", str(res_file), str(rc_file)])
    if r.returncode != 0:
        raise LauncherError(f"rc.exe couldn't build the version resource of {spec['exe']}:\n{(r.stdout or '') + (r.stderr or '')}")
    r = run([tools["cl"], "/nologo", "/O1", "/MT", "/GS", "/W3", "/DUNICODE", "/D_UNICODE", f'/DSCRIPT_NAME=L"{spec["script"]}"', f"/Fo{work}\\launcher.obj", f"/Fe{obj_exe}",
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
