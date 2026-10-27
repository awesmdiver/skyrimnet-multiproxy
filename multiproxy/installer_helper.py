"""Small jobs the Windows installers hand to the program's own Python (2026-10-02). Installed beside the program; run as
`python installer_helper.py <command> ...`. Everything it prints is one line of JSON, so the installer (which is written in Pascal) never parses prose.

  seed-multiproxy   --program-dir D --settings-dir S [--models-folder M] [--import-from OLD]
  seed-courier      --config-dir C [--models-folder M] [--port N]
  find-old-install  --folder F                      is F an old download-and-unzip MultiProxy (the kind that lives inside a Skyrim folder)?
  leftovers         --settings-dir S [--courier] [--sizes] [--program-dir P]    what an uninstall can offer to remove (one entry per kind, only what exists on this device)
  remove            --settings-dir S [--courier] --items a,b  remove the ticked kinds; anything that can't be removed comes back in one list
  forget            --program-dir D                 remove settings-location.txt (an uninstall that is keeping nothing)
  firewall-check    --name N                        is the Windows Firewall rule called N still there? (read-only, no administrator: the uninstaller asks after it asked for the rule to go)

It never scans a disk: it is only ever told a folder. It never overwrites a settings file that already exists, and never reads or prints a key.
"""
import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Optional


OLD_SETTINGS_FILES = ("config.json", "multiproxy.ini", "proxy.ini")      # what an old zip-style install keeps beside its program


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> dict:
    try:
        d = json.loads(path.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def find_old_install(folder: str) -> dict:
    """Is `folder` an old zip-style MultiProxy install? (It has the program and a config.json.) Returns what could be imported."""
    f = Path(folder)
    program = (f / "multiproxy.py").is_file() or (f / "proxy.py").is_file()
    found = [n for n in OLD_SETTINGS_FILES if (f / n).is_file()]
    return {"ok": bool(program and "config.json" in found), "folder": str(f), "files": found, "isProgram": program}


def import_old_settings(old_folder: str, settings_dir: str) -> list:
    """Copy an old install's settings into the new settings folder: only files that aren't already there, never the other way round."""
    done = []
    dest = Path(settings_dir)
    dest.mkdir(parents=True, exist_ok=True)
    for name in OLD_SETTINGS_FILES:
        src = Path(old_folder) / name
        target = dest / ("multiproxy.ini" if name == "proxy.ini" else name)
        if src.is_file() and not target.exists() and not (name == "proxy.ini" and (dest / "multiproxy.ini").exists()):
            shutil.copyfile(src, target)
            done.append(name)
    return done


def seed_multiproxy(program_dir: str, settings_dir: str, models_folder: Optional[str] = None, import_from: Optional[str] = None) -> dict:
    import settings_files                       # (imported here so the Courier, which doesn't ship it, can use the other commands)
    prog, sett = Path(program_dir), Path(settings_dir)
    sett.mkdir(parents=True, exist_ok=True)
    loc = prog / settings_files.SETTINGS_LOCATION_FILE
    try:
        same = prog.resolve() == sett.resolve()
    except OSError:
        same = False
    if same:
        loc.unlink(missing_ok=True)
    else:
        loc.write_text(str(sett), encoding="utf-8")          # the server, the tray app and the Skyrim mod all follow this (settings_files.settings_dir)
    imported = import_old_settings(import_from, settings_dir) if import_from else []
    cfg_path = sett / "config.json"
    cfg = _read_json(cfg_path)
    if models_folder:
        lm = dict(cfg.get("local_models") or {})
        if not lm.get("folder"):                                # a folder the player already chose is never replaced
            lm["folder"] = str(Path(models_folder))
            cfg["local_models"] = lm
            Path(models_folder).mkdir(parents=True, exist_ok=True)
    if cfg or not cfg_path.exists():
        _write_json(cfg_path, cfg)
    return {"ok": True, "settingsDir": str(sett), "imported": imported, "modelsFolder": (cfg.get("local_models") or {}).get("folder")}


def seed_courier(config_dir: str, models_folder: Optional[str] = None, port: Optional[int] = None, program_dir: Optional[str] = None) -> dict:
    d = Path(config_dir)
    d.mkdir(parents=True, exist_ok=True)
    if program_dir:
        (Path(program_dir) / "settings-location.txt").write_text(str(d), encoding="utf-8")      # (only the uninstaller reads this one: the Courier is told its folder on its command line)
    path = d / "courier.json"
    cfg = _read_json(path)
    if models_folder:
        lm = dict(cfg.get("local_models") or {})
        if not lm.get("folder"):
            lm["folder"] = str(Path(models_folder))
            cfg["local_models"] = lm
            cfg["models_folder_chosen"] = True
            Path(models_folder).mkdir(parents=True, exist_ok=True)
    if port:
        cfg["port"] = int(port)
    if cfg:
        _write_json(path, cfg)
    return {"ok": True, "configDir": str(d), "modelsFolder": (cfg.get("local_models") or {}).get("folder")}


def folder_size(path: Path, limit_files: int = 200000) -> int:
    total, n = 0, 0
    for root, _dirs, files in os.walk(path):
        for f in files:
            try:
                total += os.path.getsize(os.path.join(root, f))
            except OSError:
                pass
            n += 1
            if n > limit_files:
                return total
    return total


# ---- what an uninstall can offer to remove -----------------------------------------------------------------------------------------------------
ITEM_IDS = ("settings", "models", "voice", "clips", "downloads")


def _inside(child: Path, parent: Path) -> bool:
    try:
        c, p = child.resolve(), parent.resolve()
        return c != p and c.is_relative_to(p)
    except OSError:
        return False


def known_folders(settings_dir: str, courier: bool = False) -> tuple:
    """(voice folder, kept-downloads folder, models folder or None) as this install's own settings say."""
    sett = Path(settings_dir)
    cfg = _read_json(sett / ("courier.json" if courier else "config.json"))
    models = (cfg.get("local_models") or {}).get("folder") or ""
    mf = models or None
    try:
        from voice_downloads import default_voice_folder, downloads_plan      # (the program's own copy; an older install without it keeps the old places)
    except ImportError:
        default_voice_folder = lambda sd, m: Path(sd) / "voice"
        downloads_plan = lambda sd, m, e=None: {"folder": Path(e) if e else Path(sd) / "voice-downloads"}
    if courier:
        mf = models if cfg.get("models_folder_chosen") else None
        voice, downloads = default_voice_folder(sett, mf), downloads_plan(sett, mf)["folder"]
    else:
        voice = Path(cfg.get("voice_folder") or default_voice_folder(sett, mf))
        downloads = downloads_plan(sett, mf, cfg.get("voice_downloads_folder"))["folder"]
    return voice, downloads, (Path(models) if models else None)


# What an uninstall may remove from a folder it was only TOLD about (security review S8). The settings folder, the voice folder and the kept-downloads folder can be anywhere
# the player pointed them (even a folder that holds other things), so only entries with a name this program itself writes are ever offered: anything else in there stays.
SETTINGS_KNOWN = {"config.json", "courier.json", "multiproxy.ini", "proxy.ini", "settings-location.txt", "install-info.json", "local_models_pids.json",
                  "claude", "copilot", ".venv", "voice", "voice-downloads", "models", "engine"}
SETTINGS_KNOWN_PATTERNS = ("*.log", "*.log.*", "speed_history.db*", "*.tmp", ".write-test*")
VOICE_KNOWN = {"engine", "models", "voice-state.json", "voice-engine.log", "engine-trace.log", "engine-stdout.log", "server.json", "voice-license-this-pc.json", "voice-license.json", "work"}


def _known_name(name: str, known: set, patterns: tuple = ()) -> bool:
    import fnmatch
    return name in known or any(fnmatch.fnmatch(name, p) for p in patterns)


def _kept_download_entries(folder: Path) -> list:
    """The files of a kept-downloads folder that are the program's own: the ones its index names, the index, and half-finished `.part` files."""
    if not folder.is_dir():
        return []
    names = {"index.json"} | set(_read_json(folder / "index.json"))
    return [e for e in sorted(folder.iterdir()) if e.name in names or e.name.endswith(".part")]


def leftover_paths(settings_dir: str, courier: bool = False) -> dict:
    """{item id: [paths]} for everything the program can leave behind, whether or not it exists yet. Only ever the folders the program was told to use:
    the settings folder, the models folder (its `models` and `engine` folders only: models added by pointing at a file are never inside), the Higgs voice
    folder and its kept downloads. The Skyrim folder and a mod manager are not in here, and never will be."""
    sett = Path(settings_dir)
    voice, downloads, models = known_folders(settings_dir, courier)
    models = str(models) if models else ""
    out = {"models": [Path(models) / "models", Path(models) / "engine"] if models else [],
           "clips": [voice / "clips"], "voice": [], "downloads": [] if courier else _kept_download_entries(downloads)}
    if voice.is_dir():
        out["voice"] = [e for e in sorted(voice.iterdir()) if e.name != "clips" and _known_name(e.name, VOICE_KNOWN)]
    if courier and downloads.is_dir():
        out["voice"] += _kept_download_entries(downloads)       # (a Courier's kept copies are part of its voice files)
    protected = [voice, downloads] + ([Path(models)] if models else [])
    out["settings"] = [e for e in sorted(sett.iterdir()) if _known_name(e.name, SETTINGS_KNOWN, SETTINGS_KNOWN_PATTERNS)
                       and not any(e == q or _inside(q, e) or _inside(e, q) for q in protected)] if sett.is_dir() else []
    return out


def _entry_exists(path: Path) -> bool:
    try:
        return path.exists()
    except OSError:
        return False


def _has_content(path: Path) -> bool:
    if path.is_file():
        return True
    try:
        return any(True for _ in path.rglob("*"))
    except OSError:
        return False


def leftovers(settings_dir: str, courier: bool = False, sizes: bool = False, program_dir: Optional[str] = None) -> dict:
    """{"items": [{"id", "paths", "bytes"}]} in the order the uninstall page shows them. An item with nothing on disk isn't listed. `bytes` is None unless
    `sizes` (measuring a big folder takes a while; the page opens first and asks again with sizes). With `program_dir` (and `sizes`) the program's own folder
    is measured too, as a first entry `program`, so its row can show a size like the others (it is never one of the removable kinds)."""
    items = []
    if sizes and program_dir and Path(program_dir).is_dir():
        items.append({"id": "program", "paths": [str(program_dir)], "bytes": folder_size(Path(program_dir))})
    for item_id, paths in leftover_paths(settings_dir, courier).items():
        present = [q for q in paths if _entry_exists(q) and _has_content(q)]
        if not present:
            continue
        total = None
        if sizes:
            total = sum((q.stat().st_size if q.is_file() else folder_size(q)) for q in present)
        items.append({"id": item_id, "paths": [str(q) for q in present], "bytes": total})
    order = {k: i for i, k in enumerate(ITEM_IDS)}
    items.sort(key=lambda it: order.get(it["id"], -1))
    return {"items": items}


def _force_remove(path: Path, failed: list) -> None:
    def onexc(func, p, exc):
        try:
            os.chmod(p, 0o700)
            func(p)
        except OSError as e:
            failed.append({"path": str(p), "reason": (e.strerror or e.__class__.__name__)})
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, onexc=onexc)
    else:
        try:
            path.unlink()
        except OSError as e:
            try:
                os.chmod(path, 0o700)
                path.unlink()
            except OSError:
                failed.append({"path": str(path), "reason": (e.strerror or e.__class__.__name__)})


def _stop_programs_under(folder: Path) -> None:
    """A running voice engine would keep its files in use: end anything whose program file lives under `folder`."""
    if os.name != "nt" or not folder.exists():
        return
    import hidden_process
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.ExecutablePath -and $_.ExecutablePath -like '%s\\*' } | "
              "ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }" % str(folder).replace("'", "''"))
    try:
        hidden_process.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script], capture_output=True, timeout=30)
    except Exception:
        pass


def remove(settings_dir: str, items: list, courier: bool = False) -> dict:
    """Remove the named kinds. Never anything else: a name that isn't one of ITEM_IDS is ignored. Folders left empty are tidied away. A file that can't be
    removed (in use) is not an error here: it comes back, with the others, in `failed`, for the installer to report in one plain list."""
    wanted = [i for i in items if i in ITEM_IDS]
    paths = leftover_paths(settings_dir, courier)
    failed: list = []
    done = []
    voice_dir, downloads_dir, models_dir = known_folders(settings_dir, courier)
    if "voice" in wanted:
        _stop_programs_under(voice_dir / "engine")        # (a running voice engine would keep its files in use; only programs from the engine's own folder are ended)
    for item_id in wanted:
        for q in paths.get(item_id, []):
            if _entry_exists(q):
                _force_remove(q, failed)
        done.append(item_id)
    sett = Path(settings_dir)
    candidates = [voice_dir, downloads_dir, sett / "voice", sett / "voice-downloads"] + ([models_dir] if models_dir else []) + [sett]
    for d in candidates:
        try:
            if d.is_dir() and not any(d.iterdir()):
                d.rmdir()
        except OSError:
            pass
    return {"removed": done, "failed": failed}


def firewall_rule_present(name: str, netsh_text: Optional[str] = None) -> dict:
    """{"present": true|false}: is a Windows Firewall rule of this name there? Read-only (`netsh advfirewall firewall show rule name=...` needs no administrator), the same look the
    uninstall page takes before it offers the tick box. When no rule matches netsh says so without the name in it, so the name appearing is what "there" means (it holds in any language
    of Windows). `netsh_text` (or, from the command line, a file named by INSTALLER_NETSH_OUTPUT) stands in for netsh's answer: tests only, nothing here ever adds or removes a rule."""
    if netsh_text is None:
        stand_in = os.environ.get("INSTALLER_NETSH_OUTPUT")
        if stand_in:
            netsh_text = Path(stand_in).read_text(encoding="utf-8", errors="replace")
        else:
            import hidden_process
            exe = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32", "netsh.exe")
            try:
                r = hidden_process.run([exe, "advfirewall", "firewall", "show", "rule", f"name={name}"], capture_output=True, text=True, timeout=30)
                netsh_text = (r.stdout or "") + (r.stderr or "")
            except Exception:
                return {"present": None}                          # couldn't ask: the installer then looks for itself
    return {"present": name.lower() in netsh_text.lower()}


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("seed-multiproxy")
    a.add_argument("--program-dir", required=True)
    a.add_argument("--settings-dir", required=True)
    a.add_argument("--models-folder", default="")
    a.add_argument("--import-from", default="")
    b = sub.add_parser("seed-courier")
    b.add_argument("--config-dir", required=True)
    b.add_argument("--program-dir", default="")
    b.add_argument("--models-folder", default="")
    b.add_argument("--port", type=int, default=0)
    c = sub.add_parser("find-old-install")
    c.add_argument("--folder", required=True)
    d = sub.add_parser("leftovers")
    d.add_argument("--settings-dir", required=True)
    d.add_argument("--courier", action="store_true")
    d.add_argument("--sizes", action="store_true")
    d.add_argument("--program-dir", default="")
    g = sub.add_parser("remove")
    g.add_argument("--settings-dir", required=True)
    g.add_argument("--courier", action="store_true")
    g.add_argument("--items", default="")
    fw = sub.add_parser("firewall-check")
    fw.add_argument("--name", required=True)
    e = sub.add_parser("forget")
    e.add_argument("--program-dir", required=True)
    args = ap.parse_args(argv)
    if args.cmd == "seed-multiproxy":
        out = seed_multiproxy(args.program_dir, args.settings_dir, args.models_folder or None, args.import_from or None)
    elif args.cmd == "seed-courier":
        out = seed_courier(args.config_dir, args.models_folder or None, args.port or None, args.program_dir or None)
    elif args.cmd == "find-old-install":
        out = find_old_install(args.folder)
    elif args.cmd == "leftovers":
        out = leftovers(args.settings_dir, args.courier, args.sizes, args.program_dir or None)
    elif args.cmd == "remove":
        out = remove(args.settings_dir, [i for i in args.items.split(",") if i], args.courier)
    elif args.cmd == "firewall-check":
        out = firewall_rule_present(args.name)
    else:
        (Path(args.program_dir) / "settings-location.txt").unlink(missing_ok=True)
        out = {"ok": True}
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
