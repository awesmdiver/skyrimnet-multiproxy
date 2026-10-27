"""Where the app icon lives and how the tray and the windows use it (2026-10-02).

The pictures are made by scripts/make_icons.py. Installed, they sit beside the program; in the project folder they are in installer/icons/.
Every function here returns None / does nothing when a file is missing, so the caller falls back to what it drew before the icon existed.
"""
import sys
from pathlib import Path
from typing import Optional

HERE = Path(__file__).resolve().parent
NAMES = {"multiproxy": ("multiproxy.ico", "app-icon.png"), "courier": ("courier.ico", "courier-icon.png")}


def find(name: str) -> Optional[Path]:
    """The icon file `name`, beside the program or in installer/icons/; None when it isn't there."""
    for folder in (HERE, HERE / "installer" / "icons"):
        p = folder / name
        if p.is_file():
            return p
    return None


def base_image(kind: str, size: int = 64):
    """The app icon as a transparent `size` x `size` picture, ready to have the status dot drawn over its corner; None if the file is missing or unreadable."""
    path = find(NAMES[kind][1])
    if path is None:
        return None
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.convert("RGBA").resize((size, size), Image.LANCZOS)
    except Exception:
        return None


def apply_to_window(root, kind: str) -> None:
    """Give a tkinter window the app icon (title bar and taskbar). A missing or unusable file leaves the window as it was."""
    ico, png = (find(n) for n in NAMES[kind])
    try:
        if sys.platform == "win32" and ico is not None:
            root.iconbitmap(default=str(ico))
        elif png is not None:
            import tkinter
            root._app_icon = tkinter.PhotoImage(file=str(png))        # kept on the window so it isn't collected
            root.iconphoto(True, root._app_icon)
    except Exception:
        pass
