"""The app icon (2026-10-02): the files hold the listed sizes, every installer script and shortcut names the icon, and the tray falls back cleanly."""
import re
import struct
from pathlib import Path

import pytest

import app_icon
import courier_ui as cu
import multiproxy_tray as mt

ROOT = Path(__file__).resolve().parent.parent
ICONS = ROOT / "installer" / "icons"
SIZES = {16, 20, 24, 32, 40, 48, 64, 128, 256}


def ico_sizes(path: Path) -> set:
    data = path.read_bytes()
    count = struct.unpack_from("<H", data, 4)[0]
    return {(data[6 + 16 * i] or 256) for i in range(count)}


@pytest.mark.parametrize("name", ["multiproxy.ico", "courier.ico"])
def test_the_ico_files_hold_every_listed_size(name):
    assert ico_sizes(ICONS / name) == SIZES


def test_the_scripts_name_the_icon_in_each_place():
    mp = (ROOT / "installer" / "multiproxy.iss").read_text(encoding="utf-8")
    co = (ROOT / "installer" / "courier.iss").read_text(encoding="utf-8")
    for text, ico in ((mp, "multiproxy.ico"), (co, "courier.ico")):
        assert f"SetupIconFile=icons\\{ico}" in text
        assert f"UninstallDisplayIcon={{app}}\\{ico}" in text
        shortcuts = [ln for ln in text.splitlines() if ln.startswith('Name: "{auto')]
        assert shortcuts and all(f'IconFilename: "{{app}}\\{ico}"' in ln for ln in shortcuts)
    ps1 = (ROOT / "courier" / "setup-courier.ps1").read_text(encoding="utf-8")
    assert "IconLocation" in ps1 and "courier.ico" in ps1


def test_the_installers_ship_the_icon_files():
    import importlib
    bd = importlib.import_module("build") if (ROOT / "installer") in map(Path, __import__("sys").path) else None
    if bd is None:
        import sys
        sys.path.insert(0, str(ROOT / "installer"))
        bd = importlib.import_module("build")
    assert {"multiproxy.ico", "app-icon.png"} <= set(bd.PRODUCTS["multiproxy"]["data"].values())
    assert {"courier.ico", "courier-icon.png"} <= set(bd.PRODUCTS["courier"]["data"].values())


def test_the_tray_draws_the_status_dot_over_the_icon():
    for img in (mt.make_icon("green"), cu.make_icon(True)):
        assert img.size == (64, 64)
        assert img.getpixel((50, 50))[3] == 255
    assert mt.make_icon("green").getpixel((50, 50)) != mt.make_icon("red").getpixel((50, 50))
    assert mt.make_icon("green").getpixel((10, 10))[3] > 0 or mt.make_icon("green").getpixel((20, 20))[3] > 0     # the picture itself is under the dot


def test_the_tray_falls_back_to_the_drawn_icon_when_the_file_is_missing(monkeypatch):
    monkeypatch.setattr(app_icon, "find", lambda name: None)
    assert app_icon.base_image("multiproxy") is None
    assert mt.make_icon("green").getpixel((50, 50))[:3] == mt.DOT["green"][:3]
    assert cu.make_icon(True).getpixel((50, 50))[:3] == (52, 199, 89)


def test_a_window_without_the_files_is_left_alone(monkeypatch):
    monkeypatch.setattr(app_icon, "find", lambda name: None)

    class Window:
        def iconbitmap(self, *a, **k): raise AssertionError("no icon file, nothing to set")
        def iconphoto(self, *a, **k): raise AssertionError("no icon file, nothing to set")
    app_icon.apply_to_window(Window(), "courier")


def test_the_courier_package_carries_the_icon_files(tmp_path):
    import importlib.util
    spec = importlib.util.spec_from_file_location("build_pkg", ROOT / "scripts" / "build_courier_package.py")
    pkg = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pkg)
    names = pkg.build(tmp_path / "c.zip")
    assert {"courier.ico", "courier-icon.png", "app_icon.py"} <= set(names)


def test_the_couriers_icon_is_the_envelope_with_no_corner_badge():
    from PIL import Image
    big = Image.open(ICONS / "courier-icon.png").convert("RGBA")
    assert big.size == (256, 256)
    # the old badge was a light-grey disc in the bottom-left corner: that corner is empty (transparent) or envelope-dark, never light grey
    r, g, b, a = big.getpixel((22, 256 - 22))
    assert a < 40 or (r + g + b) / 3 < 120, (r, g, b, a)
    # the envelope: its body is a wide block of opaque dark pixels across the middle, at the picture's own proportions (wider than tall)
    alpha = big.getchannel("A").point(lambda v: 255 if v > 200 else 0)
    cols = [x for x in range(256) if sum(1 for y in range(0, 256, 4) if alpha.getpixel((x, y))) > 20]
    rows = [y for y in range(256) if sum(1 for x in range(0, 256, 4) if alpha.getpixel((x, y))) > 28]
    assert (max(cols) - min(cols)) > (max(rows) - min(rows)), "wider than tall: an envelope, not the stone"
    # MultiProxy's own icon is a different picture
    assert Image.open(ICONS / "app-icon.png").convert("RGB").tobytes() != big.convert("RGB").tobytes()


def test_the_picture_and_the_look_at_sheet_are_kept_in_design_icons():
    assert (ROOT / "design" / "icons" / "courier-envelope-swirl.jpg").is_file() and (ROOT / "design" / "icons" / "courier-icon-sizes.png").is_file()
