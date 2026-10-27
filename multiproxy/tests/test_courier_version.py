"""The Courier has one number: version 1.N, where N is the build (2026-10-24).

The version is worked out from the build number, never written twice, and every place a person reads it (window, log, menu, installer, package names, update lines)
says 1.N. A Courier that only reports its build still reads as 1.N.
"""
import re
import sys
from pathlib import Path

import pytest

import courier as cr
import courier_ui as ui
import other_pcs as op

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
sys.path.insert(0, str(ROOT / "installer"))
import build as installer_build  # noqa: E402
import build_courier_package as pkg  # noqa: E402
import build_linux_packages as linux  # noqa: E402
import courier_files  # noqa: E402


def test_the_version_is_the_build_and_cannot_drift():
    assert cr.COURIER_VERSION == f"1.{cr.COURIER_BUILD}"
    source = (ROOT / "courier.py").read_text(encoding="utf-8")
    assert re.search(r"^COURIER_BUILD = \d+\s*$", source, re.M), "the updater, the signer and the build scripts read this plain line"
    assert not re.search(r'^COURIER_VERSION\s*=\s*"', source, re.M), "no second, hand-written version"
    assert courier_files.version_of(source) == cr.COURIER_VERSION


def test_the_build_scripts_and_the_signer_read_the_same_version():
    assert installer_build.read_version(installer_build.PRODUCTS["courier"]) == cr.COURIER_VERSION
    assert linux.version_of("courier") == cr.COURIER_VERSION
    assert installer_build.PRODUCTS["courier"]["output"].format(version=cr.COURIER_VERSION) == f"MultiProxy-Courier-Setup-{cr.COURIER_VERSION}"
    assert linux.PRODUCTS["courier"]["output"].format(version=cr.COURIER_VERSION) == f"MultiProxy-Courier-{cr.COURIER_VERSION}-linux.tar.gz"
    import sign_courier_package as sign
    files = {sign.uv.BUILD_FILE: (ROOT / "courier.py").read_bytes(), sign.uv.REQUIREMENTS_FILE: b"aiohttp\n"}
    manifest = __import__("json").loads(sign.build_manifest(files))
    assert manifest["version"] == cr.COURIER_VERSION and manifest["build"] == cr.COURIER_BUILD


def test_the_zip_is_named_by_the_version():
    ap_default = (ROOT / "scripts" / "build_courier_package.py").read_text(encoding="utf-8")
    assert "MultiProxy-Courier-{courier_files.version_of(" in ap_default and ".zip" in ap_default


def test_the_menu_window_and_log_say_one_number():
    assert ui.version_line() == f"MultiProxy Courier {cr.COURIER_VERSION}"
    m = ui.menu_model(True, "Den PC", "AAA-111", True, True, paired_name="Den PC")
    assert m[0]["text"] == "MultiProxy Courier" and m[1]["text"] == f"{cr.COURIER_VERSION} · Connected to Den PC"
    assert "build" not in m[1]["text"].lower()
    assert ui.build_line({"courier": {"build": 4}}) == "Version 1.4"
    assert ui.build_line({"courier": {"build": 4, "built": "2026-10-24-abc1234"}}) == "Version 1.4 · 2026-10-24-abc1234"
    assert ui.build_line({"courier": {}}) == ""


def test_an_older_courier_that_only_reports_its_build_reads_as_1_n():
    assert op.courier_version_of(4) == "1.4" and op.courier_version_of(6) == "1.6"
    assert op.courier_build_of({"courier": {"version": "1.0.0", "build": 4}}) == 4          # the old report's own version string is not what is shown


def test_the_update_lines_use_the_version():
    import courier_update_sender as s
    assert s.version_of(4) == "1.4"
    src = (ROOT / "courier_update_sender.py").read_text(encoding="utf-8")
    assert "is too old to update from here. Update it on that device once." in src and "build {job" not in src
