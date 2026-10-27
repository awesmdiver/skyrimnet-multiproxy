"""Builds the two Windows installers (2026-10-02):  dist/MultiProxy-Setup-<version>.exe  and  dist/MultiProxy-Courier-Setup-<version>.exe

    python installer/build.py [--only multiproxy|courier] [--mod-package PATH] [--iscc PATH] [--out DIR]
    (or  installer\\build-installers.ps1 , which finds Python and Inno Setup for you)

What it does, in order, for each installer:
  1. Runtime: python.org's embeddable Python 3.14.10 (pinned; its SHA-256 is checked against the value python.org publishes before anything uses it; cached in
     installer/.cache/, never committed), with every dependency installed into it NOW, from the product's lock file (installer/locks/: exact versions and file hashes) (so nothing is downloaded when the player installs) and tkinter added
     (the embeddable build has none; it is copied from the same-version Python this build runs on, because the log window needs it).
  2. Program files: the real list, followed from what multiproxy.py / multiproxy_tray.py (or courier.py) import, plus the few data files they read. Nothing
     that holds a secret (config.json, courier.json, the ini, logs, the speed history) can ever be staged: the build refuses.
  3. A smoke test with the BUNDLED python (system Python hidden): every third-party and program module imports.
  4. Inno Setup compiles the installer.
Plain-language failures: the build says what is missing and what to do, never a traceback.
"""
import argparse
import ast
import hashlib
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parent / "launcher"))
import build_launcher         # the small start programs (installer/launcher/): "SkyrimNet MultiProxy.exe" and "MultiProxy Courier.exe"
import courier_files          # THE list of the Courier's files (courier/courier-files.json), shared with the zip, the Linux package, the set-up script and the signer
HERE = Path(__file__).resolve().parent
CACHE = HERE / ".cache"
STAGE = HERE / ".stage"

PY_VERSION = "3.14.10"
PY_TAG = "314"
EMBED_NAME = f"python-{PY_VERSION}-embed-amd64.zip"
EMBED_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/{EMBED_NAME}"
EMBED_SHA256 = "90905e64a840557d8c1bc4f5b74db3d7ee85e3ffb217611cac1c7813acea4159"      # python.org's published value for this file (release page, 2026-10-26; the download hashes to the same)

NEVER = {"courier-signing-key.txt", "signin.dat", "config.json", "courier.json", "multiproxy.ini", "multiproxy.log", "proxy.ini", "proxy.log", "courier.log", "speed_history.db", "local_models_pids.json",
         "settings-location.txt", "install-info.json", "config.before-last-load.json"}

PRODUCTS = {
    "multiproxy": {
        "roots": ["multiproxy", "multiproxy_tray", "installer_helper"],
        "lock": "installer/locks/multiproxy.lock.txt",
        "data": {"docs/help/help-content.md": "docs/help/help-content.md", "multiproxy.ini.example": "multiproxy.ini.example", "config.example.json": "config.example.json", "settings_presets.json": "settings_presets.json",
                 "LICENSE": "LICENSE-proxy.txt", "THIRD-PARTY-NOTICES.md": "THIRD-PARTY-NOTICES.md", "requirements.txt": "requirements.txt", "requirements-tray.txt": "requirements-tray.txt",
                 "installer/icons/multiproxy.ico": "multiproxy.ico", "installer/icons/app-icon.png": "app-icon.png"},
        "smoke_modules": ["multiproxy", "multiproxy_tray", "installer_helper", "voice_wiring", "local_channel"],
        "version_file": "multiproxy.py", "version_re": r'^PROXY_VERSION\s*=\s*"([^"]+)"',
        "iss": "multiproxy.iss", "output": "MultiProxy-Setup-{version}",
    },
    "courier": {
        "roots": ["courier", "installer_helper"],
        "lock": "installer/locks/courier.lock.txt",
        "data": {src: dest for dest, src in courier_files.data().items()},
        "file_list": "courier",          # its program files come from courier-files.json, not from following imports (tests/test_courier_files.py checks the two agree)
        "smoke_modules": ["courier", "courier_ui", "installer_helper"],
        "smoke_skip": ["fastapi"],      # only a function-level import in gpu_choice_wiring.setup(), which the main device's server runs and the Courier never does
        "version_file": "courier.py", "version_from": courier_files.version_of,
        "iss": "courier.iss", "output": "MultiProxy-Courier-Setup-{version}",
    },
}


class BuildError(Exception):
    """A plain reason the build can't go on."""


def rmtree(path, ignore_errors: bool = False) -> None:
    """Delete a folder even when some files in it are read-only (files copied from the system Python are)."""
    def onexc(func, p, exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError:
            if not ignore_errors:
                raise
    shutil.rmtree(path, onexc=onexc)


def say(msg: str) -> None:
    print(msg, flush=True)


def run(cmd: list, **kw) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


# ---------------------------------------------------------------------------------------------------------------------------------
# The program's files
# ---------------------------------------------------------------------------------------------------------------------------------
def _imports(path: Path) -> set:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    out = set()
    for n in ast.walk(tree):
        if isinstance(n, ast.Import):
            out |= {a.name.split(".")[0] for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.module and n.level == 0:
            out.add(n.module.split(".")[0])
    return out


def product_modules(name: str) -> list:
    """The program modules (names without .py) a product ships: the Courier's come from the one shared list, the others are followed from their roots."""
    product = PRODUCTS[name]
    if product.get("file_list") == "courier":
        return sorted(f[:-3] for f in courier_files.program() + courier_files.installer_only())
    return program_modules(product["roots"])


def program_modules(roots: list, root: Path = ROOT) -> list:
    """Every program module the roots import, directly or through each other (lazy imports inside functions included), sorted."""
    local = {p.stem for p in root.glob("*.py")}
    seen, todo = set(), list(roots)
    while todo:
        m = todo.pop()
        if m in seen:
            continue
        if m not in local:
            raise BuildError(f"{m}.py is named as part of the program but isn't in the project folder.")
        seen.add(m)
        todo += [x for x in _imports(root / f"{m}.py") if x in local]
    return sorted(seen)


def third_party(modules: list, root: Path = ROOT) -> list:
    local = {p.stem for p in root.glob("*.py")}
    out = set()
    for m in modules:
        out |= {x for x in _imports(root / f"{m}.py") if x not in local and x not in sys.stdlib_module_names}
    return sorted(out)


def read_version(product: dict) -> str:
    text = (ROOT / product["version_file"]).read_text(encoding="utf-8")
    if "version_from" in product:          # the Courier's version is 1.<build>, worked out from the build number
        try:
            return product["version_from"](text)
        except ValueError as e:
            raise BuildError(str(e))
    m = re.search(product["version_re"], text, re.M)
    if not m:
        raise BuildError(f"Couldn't read the version from {product['version_file']}.")
    return m.group(1)


# The signed Courier update MultiProxy carries and sends to other devices (scripts/sign_courier_package.py makes it; docs/RELEASING-THE-COURIER.md). Without it a build still works,
# the Update Courier buttons just don't show; a release is built with --require-courier-update.
COURIER_UPDATE_SOURCE = ROOT / "dist" / "MultiProxy-Courier-Update.zip"
COURIER_UPDATE_NAME = "courier-update/MultiProxy-Courier-Update.zip"
REQUIRE_COURIER_UPDATE = False
SKIP_OSV = False


def current_courier_build() -> int:
    """The build number courier.py is at now (what the signed update that MultiProxy carries has to be)."""
    m = re.search(r"^COURIER_BUILD\s*=\s*(\d+)", (ROOT / "courier.py").read_text(encoding="utf-8"), re.M)
    if not m:
        raise BuildError("courier.py does not state its COURIER_BUILD.")
    return int(m.group(1))


def check_courier_build(found, expected: int = None) -> None:
    """Stops the build when the signed Courier update is not the build the code is at (a stale package left in dist/ would be carried without a word otherwise)."""
    expected = current_courier_build() if expected is None else expected
    if found != expected:
        raise BuildError(f"The signed Courier update is build {found}, but the Courier code is at build {expected}. Sign build {expected} (docs/RELEASING-THE-COURIER.md) "
                         f"and put it at dist/MultiProxy-Courier-Update.zip, then build again.")


def carry_courier_update(dest: Path, source: Path = COURIER_UPDATE_SOURCE, required: bool = False) -> list:
    """Put the signed Courier update into a MultiProxy stage, after checking it with the very code the program uses (a bad file is never shipped). Returns the names added."""
    if not source.is_file():
        if required:
            raise BuildError(f"The signed Courier update isn't there ({source}). Build the Courier, sign it (docs/RELEASING-THE-COURIER.md), then build again.")
        say("  no signed Courier update next to this build: the Update Courier buttons will not show")
        return []
    import update_verify
    try:
        manifest, _ = update_verify.verify_package(source)
    except update_verify.UpdateRejected as e:
        raise BuildError(f"The signed Courier update does not pass its own check ({e.code}); it was not added. Sign it again with the key courier_keys.py trusts.")
    check_courier_build(manifest.get("build"))
    target = dest / COURIER_UPDATE_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(source, target)
    say(f"  carries the signed Courier update, build {manifest['build']}")
    return [COURIER_UPDATE_NAME]


def stage_program(name: str, dest: Path) -> list:
    """Copy exactly the program files and data files into `dest` (emptied first). Returns the relative names."""
    product = PRODUCTS[name]
    if dest.exists():
        rmtree(dest)
    dest.mkdir(parents=True)
    files = [f"{m}.py" for m in product_modules(name)]
    entries = [(ROOT / f, f) for f in files] + [(ROOT / src, d) for src, d in product["data"].items()]
    for src, d in entries:
        if Path(d).name in NEVER or Path(d).name.endswith(".tmp") or "claude" in Path(d).parts[:-1]:
            raise BuildError(f"Refusing to package {d}: it holds a player's own settings or secrets and must never be in an installer.")
        if not src.is_file():
            raise BuildError(f"Can't build: {src} is missing.")
        target = dest / d
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, target)
    names = sorted(d for _, d in entries)
    if name == "multiproxy":
        sys.path.insert(0, str(ROOT))
        names = sorted(names + carry_courier_update(dest, required=REQUIRE_COURIER_UPDATE))
    if name in ("courier", "multiproxy"):        # the date and commit this build is from: the Courier shows it and sends it in its report (courier.read_built); MultiProxy shows it in
        sys.path.insert(0, str(ROOT / "scripts"))      # its tray menu, its About line and the first line of its log (build_info.py)
        from build_courier_package import STAMP_NAME, build_stamp
        stamp_name = STAMP_NAME if name == "courier" else "multiproxy-build.txt"
        (dest / stamp_name).write_text(build_stamp() + "\n", encoding="utf-8")
        names = sorted(names + [stamp_name])
    return names


# ---------------------------------------------------------------------------------------------------------------------------------
# The bundled Python
# ---------------------------------------------------------------------------------------------------------------------------------
def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def get_embed_zip() -> Path:
    CACHE.mkdir(exist_ok=True)
    p = CACHE / EMBED_NAME
    if not p.exists():
        say(f"Downloading python.org's embeddable Python {PY_VERSION} ...")
        tmp = p.with_suffix(".part")
        try:
            urllib.request.urlretrieve(EMBED_URL, tmp)
        except OSError as e:
            raise BuildError(f"Couldn't download {EMBED_URL} ({e}). Check the internet connection and run the build again.")
        os.replace(tmp, p)
    got = sha256_of(p)
    if got != EMBED_SHA256:
        p.unlink()
        raise BuildError(f"The embeddable Python download didn't match python.org's published checksum (got {got[:12]}..., expected {EMBED_SHA256[:12]}...). It was deleted; run the build again.")
    return p


def host_python_matches() -> Path:
    """tkinter can't be downloaded separately: it is copied from the Python this build runs on, which must be the same version as the one it goes into."""
    if sys.version.split()[0] != PY_VERSION:
        raise BuildError(f"This build needs to run on Python {PY_VERSION} (it copies tkinter from it into the installer's own Python), but this is {sys.version.split()[0]}. "
                         f"Install Python {PY_VERSION} (python.org) and run the build with it.")
    base = Path(sys.base_prefix)
    for need in ("DLLs/_tkinter.pyd", "tcl", "Lib/tkinter"):
        if not (base / need).exists():
            raise BuildError(f"This Python has no tkinter ({need} is missing). Install Python {PY_VERSION} with its 'tcl/tk' option and run the build again.")
    return base


def build_runtime(name: str, dest: Path) -> None:
    product = PRODUCTS[name]
    zip_path = get_embed_zip()
    host = host_python_matches()
    if dest.exists():
        rmtree(dest)
    dest.mkdir(parents=True)
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(dest)
    # The embeddable build is isolated: it ignores site-packages unless its ._pth file says otherwise.
    pth = dest / f"python{PY_TAG}._pth"
    # (".." is the program folder, the runtime's parent: an embeddable Python never puts a script's own folder on its path, so without this line
    # `python multiproxy.py` could not import its sibling modules.)
    pth.write_text(f"python{PY_TAG}.zip\n.\nLib\\site-packages\n..\nimport site\n", encoding="utf-8")
    sp = dest / "Lib" / "site-packages"
    sp.mkdir(parents=True)
    # Dependencies, installed now, as wheels only (a source build would need a compiler on the player's device, and there is none there), from the lock file: every package
    # at its exact version, and a file whose hash is not in the lock is refused (scripts/make_runtime_lock.py writes it).
    cmd = [sys.executable, "-m", "pip", "install", "--disable-pip-version-check", "--no-warn-script-location", "--only-binary=:all:", "--require-hashes", "--no-deps", "--target", str(sp),
           "--platform", "win_amd64", "--python-version", PY_VERSION.rsplit(".", 1)[0], "--implementation", "cp", "--abi", f"cp{PY_TAG}", "-r", str(ROOT / product["lock"])]
    res = run(cmd)
    if res.returncode != 0:
        raise BuildError("Couldn't install the program's Python packages into the bundled Python (they must all be available as ready-made wheels):\n" + res.stderr[-1500:])
    # tkinter (+ the Tcl/Tk runtime it needs), same version, from the Python this build runs on.
    for src in list((host / "DLLs").glob("tcl*.dll")) + list((host / "DLLs").glob("tk*.dll")) + [host / "DLLs" / "_tkinter.pyd", host / "DLLs" / "zlib1.dll"]:
        if src.exists():
            shutil.copyfile(src, dest / src.name)
    shutil.copytree(host / "Lib" / "tkinter", sp / "tkinter", ignore=shutil.ignore_patterns("__pycache__", "test", "tests"))
    shutil.copytree(host / "tcl", dest / "tcl", ignore=shutil.ignore_patterns("__pycache__", "demos", "*.lib", "nmake", "*.sh"))
    for junk in list(dest.rglob("__pycache__")):
        rmtree(junk, ignore_errors=True)


def runtime_for(name: str) -> Path:
    """The cached runtime for this product: rebuilt only when the pinned Python or the lock file's text changes."""
    product = PRODUCTS[name]
    key = hashlib.sha256((EMBED_SHA256 + (ROOT / product["lock"]).read_text(encoding="utf-8")).encode()).hexdigest()[:12]
    dest = CACHE / f"runtime-{name}-{PY_VERSION}-{key}"
    if not (dest / "python.exe").exists():
        say(f"Building the bundled Python for {name} (first time, or the lock file changed) ...")
        build_runtime(name, dest)
    return dest


def osv_gate(runtime: Path, skip: bool = False) -> None:
    """Asks OSV (names and versions only) about every package in the built runtime, on every build (a cached runtime meets advisories published after it was built),
    and stops the build on a known vulnerability that a newer version fixes, saying which. Cannot ask: it stops too, unless this is a trial build run with --skip-osv-check."""
    import osv_check
    if skip:
        say("  OSV check SKIPPED (--skip-osv-check): do not release from this build")
        return
    try:
        found = osv_check.check(osv_check.installed(runtime / "Lib" / "site-packages"))
    except osv_check.OsvUnavailable as e:
        raise BuildError(f"Couldn't ask OSV (osv.dev) whether the bundled packages have known vulnerabilities ({e}). Check the internet connection and build again; "
                         "for a trial build that will not be released, add --skip-osv-check.")
    if found:
        raise BuildError("A package in the bundled Python has a known vulnerability that a newer version fixes:\n" + osv_check.describe(found)
                         + "\nMove its pin: run scripts/make_runtime_lock.py, read the diff, and build again.")
    say("  OSV check: no known vulnerability with a fix available")


# ---------------------------------------------------------------------------------------------------------------------------------
# The smoke test
# ---------------------------------------------------------------------------------------------------------------------------------
def smoke_test(name: str, runtime: Path, stage: Path, modules: list) -> None:
    """Assemble the real layout (program files with the runtime in a python folder beside them, by hard links so it is instant), then run the BUNDLED python
    (not this one: the system Python is not on its path) and import everything the program imports."""
    product = PRODUCTS[name]
    third = [t for t in third_party(modules) if t not in product.get("smoke_skip", [])]
    box = CACHE / f"smoke-{name}"
    if box.exists():
        rmtree(box)
    shutil.copytree(stage, box)
    try:
        shutil.copytree(runtime, box / "python", copy_function=os.link)
    except OSError:
        shutil.copytree(runtime, box / "python")
    code = "\n".join([
        "import sys, importlib",
        f"third = {third!r}",
        f"program = {product['smoke_modules']!r}",
        "for m in third + ['tkinter', 'ssl', 'sqlite3', 'asyncio', 'json', 'zipfile']:",
        "    importlib.import_module(m)",
        "import tkinter; root = tkinter.Tcl(); root.eval('info patchlevel')",
        "for m in program:",
        "    importlib.import_module(m)",
        "print('ok', sys.version.split()[0], len(third), 'packages', len(program), 'program modules')",
    ]) + "\n"
    env = {"SystemRoot": os.environ.get("SystemRoot", r"C:\Windows"), "PATH": str(box / "python"), "MULTIPROXY_SETTINGS_DIR": str(CACHE / "smoke-settings"),
           "PYTHONDONTWRITEBYTECODE": "1"}
    (CACHE / "smoke-settings").mkdir(parents=True, exist_ok=True)
    # run a SCRIPT from the program folder, as the tray does with multiproxy.py (this is what the ".." in the runtime's path file is for)
    (box / "_smoke.py").write_text(code, encoding="utf-8")
    res = run([str(box / "python" / "python.exe"), "_smoke.py"], cwd=str(box), env=env)
    rmtree(box, ignore_errors=True)
    if res.returncode != 0 or not res.stdout.startswith("ok"):
        raise BuildError("The bundled Python can't run the program (the smoke test failed):\n" + (res.stderr or res.stdout)[-2000:])
    say("  smoke test: " + res.stdout.strip())


# ---------------------------------------------------------------------------------------------------------------------------------
# The mod package, Inno Setup
# ---------------------------------------------------------------------------------------------------------------------------------
MOD_PACKAGE_NAME = "SkyrimNetMultiProxy.zip"       # the same name every release, so a mod manager sees an update over the old one, not a second mod


def find_mod_package(explicit: str = "") -> Path:
    """`SkyrimNetMultiProxy.zip` (the same name every release) from the public repo's build output. Named explicitly, or by MULTIPROXY_MOD_PACKAGE, or found in the public
    repo's release-staging folder (MULTIPROXY_PUBLIC_REPO, default: the folder beside this project named skyrimnet-multiproxy)."""
    for cand in (explicit, os.environ.get("MULTIPROXY_MOD_PACKAGE", "")):
        if cand:
            p = Path(cand)
            if not p.is_file():
                raise BuildError(f"The Skyrim mod package {p} isn't there.")
            return p
    repo = Path(os.environ.get("MULTIPROXY_PUBLIC_REPO") or ROOT.parent / "skyrimnet-multiproxy")
    found = sorted((repo / "release-staging").glob(MOD_PACKAGE_NAME)) if (repo / "release-staging").is_dir() else []
    if not found:
        raise BuildError("The Skyrim mod package (SkyrimNetMultiProxy.zip) isn't built yet. It comes from the public repo "
                         f"({repo}, in release-staging); build it there first, or pass --mod-package PATH to use one you have.")
    return found[-1]


MOD_DLL = "SKSE/Plugins/SkyrimNetMultiProxy.dll"
_BETA_LABEL = re.compile(rb"[-_. ]beta[-_. ]?\d", re.IGNORECASE)
_COMMONLIB = re.compile(rb"commonlib", re.IGNORECASE)
_ADDRESS_LIBRARY = re.compile(rb"versionlib-", re.IGNORECASE)


def _in_dll(pattern, dll: bytes) -> bool:
    """True when the pattern is in the file as plain text (ASCII) or as UTF-16 text."""
    return bool(pattern.search(dll) or pattern.search(dll.decode("utf-16-le", "ignore").encode("utf-8")))


def check_mod_package(path: Path) -> None:
    """The one mod file everyone gets is a single DLL, written against SKSE's plain plugin interface (MIT, like the rest of MultiProxy): no licence folder, no beta label,
    no CommonLibSSE and no Address Library in it. Stops the build, saying what is wrong, so a wrong zip never ends up in an installer."""
    try:
        zf = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError):
        raise BuildError(f"The Skyrim mod package {path} isn't a zip file.")
    with zf:
        names = sorted(i.filename for i in zf.infolist() if not i.is_dir())
        if names != [MOD_DLL]:
            missing = [] if MOD_DLL in names else [MOD_DLL]
            extra = [n for n in names if n != MOD_DLL]
            raise BuildError("The Skyrim mod package must hold the one mod file and nothing else. "
                             + (f"Missing: {', '.join(missing)}. " if missing else "") + (f"Not allowed: {', '.join(extra)}. " if extra else "")
                             + "Rebuild it in the public repo.")
        dll = zf.read(MOD_DLL)
    if _in_dll(_BETA_LABEL, dll):
        raise BuildError("The Skyrim mod file still has a beta label in it. Rebuild it from the final version.")
    if _in_dll(_COMMONLIB, dll):
        raise BuildError("The Skyrim mod file still has CommonLibSSE in it. It must be the build that needs no extra library. Rebuild it in the public repo.")
    if _in_dll(_ADDRESS_LIBRARY, dll):
        raise BuildError("The Skyrim mod file still names an Address Library file. It must work without the Address Library. Rebuild it in the public repo.")


def find_iscc(explicit: str = "") -> Path:
    cands = [explicit, os.environ.get("ISCC", ""), shutil.which("iscc") or "",
             str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Inno Setup 6" / "ISCC.exe"),
             r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe", r"C:\Program Files\Inno Setup 6\ISCC.exe"]
    for c in cands:
        if c and Path(c).is_file():
            return Path(c)
    raise BuildError("Inno Setup isn't installed. Install it (free) with:  winget install --id JRSoftware.InnoSetup --scope user  and run the build again.")


def compile_installer(name: str, version: str, stage: Path, runtime: Path, mod: Path, iscc: Path, out: Path) -> Path:
    product = PRODUCTS[name]
    out.mkdir(parents=True, exist_ok=True)
    base = product["output"].format(version=version)
    cmd = [str(iscc), "/Q", f"/DAppVersion={version}", f"/DStageDir={stage}", f"/DRuntimeDir={runtime}", f"/DOutputDir={out}", f"/DOutputBase={base}",
           f"/DSharedDir={HERE}"]
    if name == "multiproxy":
        cmd.append(f"/DModPackage={mod}")
    else:       # what an older zip-file Courier left in its folder: every file the one shared list names, plus the batch file the zip setup made
        cmd.append("/DOlderProgramFiles=" + ",".join(courier_files.program() + list(courier_files.data()) + ["Start Courier.bat"]))
    cmd.append(str(HERE / product["iss"]))
    res = run(cmd)
    if res.returncode != 0:
        text = res.stdout + res.stderr
        hint = ""
        if "WizardStyle" in text:
            hint = ("\n\nThe installers follow Windows' light or dark setting with `WizardStyle=modern dynamic`, which this Inno Setup is too old for "
                    "(built and checked with 6.7.3). Update it:  winget upgrade --id JRSoftware.InnoSetup")
        raise BuildError("Inno Setup couldn't compile the installer:\n" + text[-2500:] + hint)
    exe = out / f"{base}.exe"
    if not exe.is_file():
        raise BuildError(f"Inno Setup finished but {exe} isn't there.")
    return exe


def build(name: str, mod_package: str, iscc: str, out: Path) -> Path:
    product = PRODUCTS[name]
    version = read_version(product)
    say(f"== {name} {version}")
    stage = STAGE / name
    files = stage_program(name, stage)
    start_exe = build_launcher.build(name, version, stage / build_launcher.PRODUCTS[name]["exe"])          # its own name in Windows' Startup list (installer/launcher/)
    say(f"  start program {start_exe.name} built ({start_exe.stat().st_size // 1024} KB)")
    for junk in stage.rglob("__pycache__"):
        rmtree(junk, ignore_errors=True)
    say(f"  {len(files)} program files staged")
    runtime = runtime_for(name)
    osv_gate(runtime, SKIP_OSV)
    smoke_test(name, runtime, stage, product_modules(name))
    mod = find_mod_package(mod_package) if name == "multiproxy" else Path()
    if name == "multiproxy":
        check_mod_package(mod)
    if name == "multiproxy" and mod.name != MOD_PACKAGE_NAME:
        # the installer ships it under the fixed name whatever the file on disk is called
        CACHE.mkdir(parents=True, exist_ok=True)
        named = CACHE / "mod" / MOD_PACKAGE_NAME
        named.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(mod, named)
        mod = named
    exe = compile_installer(name, version, stage, runtime, mod, find_iscc(iscc), out)
    say(f"  built {exe} ({exe.stat().st_size / 1e6:.1f} MB)")
    return exe


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--only", choices=list(PRODUCTS))
    ap.add_argument("--mod-package", default="")
    ap.add_argument("--iscc", default="")
    ap.add_argument("--out", default=str(ROOT / "dist"))
    ap.add_argument("--require-courier-update", action="store_true", help="refuse to build MultiProxy without the signed Courier update (a release does)")
    ap.add_argument("--skip-osv-check", action="store_true", help="trial builds only (no internet): skip the known-vulnerability check; a release build (--require-courier-update) refuses this")
    args = ap.parse_args(argv)
    if args.skip_osv_check and args.require_courier_update:
        print("\nThe build stopped: a release build always runs the known-vulnerability check; drop --skip-osv-check.", file=sys.stderr)
        return 1
    global REQUIRE_COURIER_UPDATE, SKIP_OSV
    REQUIRE_COURIER_UPDATE, SKIP_OSV = args.require_courier_update, args.skip_osv_check
    try:
        for name in ([args.only] if args.only else list(PRODUCTS)):
            build(name, args.mod_package, args.iscc, Path(args.out))
    except BuildError as e:
        print(f"\nThe build stopped: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
