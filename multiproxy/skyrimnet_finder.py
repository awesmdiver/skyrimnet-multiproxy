"""Finding SkyrimNet's own folder (`SKSE\\Plugins\\SkyrimNet`), also for Mod Organizer 2 players (2026-10-25). Nothing from MultiProxy is imported here, and nothing is written.

Where it can be:
  * a normal install: `<game>\\Data\\SKSE\\Plugins\\SkyrimNet`;
  * an MO2 mod: `<instance>\\mods\\<mod>\\SKSE\\Plugins\\SkyrimNet`. MO2 stores a mod as the CONTENTS of the Data folder, so there is no `Data` level inside it (the layout the first
    finder missed, found 2026-10-25 from a Steam-folder MO2 player); `mods\\<mod>\\Data\\SKSE\\...` is kept too, and so is `overwrite` (with or without `Data`).
A candidate counts only when it has a `config` folder. Several candidates: the one in the active profile's order when `profiles\\<name>\\modlist.txt` can be read (enabled mods
only; MO2 writes the highest priority first), else the one whose `SkyrimNet.yaml` is newest. `mods` beats `overwrite`, as before.

`from_pick` works out the SkyrimNet folder from whatever folder the player picked when asked "Where is your SkyrimNet folder?": the SkyrimNet folder itself, the mod's own folder
(the one that holds `SKSE`), an MO2 instance (holds `mods`), a `mods` folder, or a game folder. Nothing is guessed beyond that.
"""
import os
from typing import Optional

_PLUGIN = ("SKSE", "Plugins", "SkyrimNet")


def _valid(candidate: str) -> bool:
    return os.path.isdir(os.path.join(candidate, "config"))


def _in_mod(mod_dir: str) -> Optional[str]:
    """SkyrimNet's folder inside one mod (or `overwrite`): directly under it (MO2's layout) or under a `Data` folder."""
    for c in (os.path.join(mod_dir, *_PLUGIN), os.path.join(mod_dir, "Data", *_PLUGIN)):
        if _valid(c):
            return c
    return None


def _yaml_mtime(candidate: str) -> float:
    try:
        return os.path.getmtime(os.path.join(candidate, "config", "SkyrimNet.yaml"))
    except OSError:
        return -1.0


def _profile_order(instance: str) -> Optional[list]:
    """The enabled mods of the instance's active profile, highest priority first, or None when it can't be read (then the newest SkyrimNet.yaml decides)."""
    name = ""
    try:
        with open(os.path.join(instance, "ModOrganizer.ini"), "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                if line.strip().lower().startswith("selected_profile="):
                    name = line.split("=", 1)[1].strip()
                    if name.lower().startswith("@bytearray(") and name.endswith(")"):
                        name = name[len("@bytearray("):-1]
                    break
    except OSError:
        pass
    profiles = os.path.join(instance, "profiles")
    if not name:
        try:
            names = [n for n in os.listdir(profiles) if os.path.isfile(os.path.join(profiles, n, "modlist.txt"))]
        except OSError:
            return None
        if len(names) != 1:
            return None
        name = names[0]
    if os.sep in name or "/" in name or name in ("", ".", ".."):
        return None
    try:
        with open(os.path.join(profiles, name, "modlist.txt"), "r", encoding="utf-8", errors="replace") as f:
            lines = f.read().splitlines()
    except OSError:
        return None
    return [ln[1:].strip() for ln in lines if ln.startswith("+") and ln[1:].strip()]


def from_instance(instance: str, mods_dir: Optional[str] = None, overwrite: Optional[str] = None) -> Optional[str]:
    """Search an MO2 instance (or just a `mods` folder when `instance` is None)."""
    mods_dir = mods_dir or os.path.join(instance, "mods")
    found = []
    try:
        names = os.listdir(mods_dir)
    except OSError:
        names = []
    for name in names:
        c = _in_mod(os.path.join(mods_dir, name))
        if c:
            found.append((name, c))
    if found:
        order = _profile_order(instance) if instance else None
        if order:
            ranked = [(order.index(n), c) for n, c in found if n in order]
            if ranked:
                return min(ranked)[1]
            # none of the mods with a SkyrimNet folder is enabled in the profile: fall through to the newest, never to nothing
        return sorted((c for _, c in found), key=_yaml_mtime, reverse=True)[0]
    if overwrite is None and instance:
        overwrite = os.path.join(instance, "overwrite")
    return _in_mod(overwrite) if overwrite else None


def from_game_root(skyrim_path: str) -> Optional[str]:
    """The linked Skyrim folder: the normal place, then the MO2 instance next to it (the game folder's parent holds `mods`, as for a Stock Game folder)."""
    direct = os.path.join(skyrim_path, "Data", *_PLUGIN)
    if _valid(direct):
        return direct
    parent = os.path.dirname(os.path.normpath(skyrim_path))
    return from_instance(parent)


def from_pick(picked: str) -> Optional[str]:
    """The SkyrimNet folder for a folder the player picked, or None when it holds none."""
    if not picked or not os.path.isdir(picked):
        return None
    picked = os.path.normpath(picked)
    if os.path.basename(picked).lower() == "skyrimnet" and _valid(picked):
        return picked                                                     # the SkyrimNet folder itself
    c = _in_mod(picked)                                                   # the mod's own folder (holds SKSE), or a folder with Data\SKSE
    if c:
        return c
    if os.path.isdir(os.path.join(picked, "mods")):                       # an MO2 instance
        return from_instance(picked)
    c = from_instance(None, mods_dir=picked)                              # a mods folder: its children are the mods
    if c:
        return c
    return from_game_root(picked)                                         # a game folder


def resolve(skyrim_path: str, pick: str = "") -> Optional[str]:
    """What MultiProxy uses: the folder the player picked when it still holds SkyrimNet, else the one found from the linked Skyrim folder."""
    if pick:
        got = from_pick(pick)
        if got:
            return got
    return from_game_root(skyrim_path) if skyrim_path else None


# Every word of the "Where is your SkyrimNet folder?" question, in one place (Settings and Get started both use it; the design side swaps them here).
WORDS = {
    "ask": "Where is your SkyrimNet folder?",
    "browse": "Browse…",
    "hint": "Using Mod Organizer 2? Pick your MO2 folder (the one that holds your mods).",
    "found": "Found SkyrimNet in {folder}",
    "none": "That folder does not hold SkyrimNet. Pick another.",
}
