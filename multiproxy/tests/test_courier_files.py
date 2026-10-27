"""The Courier's ONE list of files (courier/courier-files.json). Every build reads it; these tests fail when a program file the Courier imports is not on it, or
when a consumer goes back to keeping a copy of its own."""
import importlib.util
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import courier_files  # noqa: E402


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


build_pkg = load("scripts/build_courier_package.py", "files_build_pkg")
installer_build = load("installer/build.py", "files_installer_build")
linux_build = load("scripts/build_linux_packages.py", "files_linux_build")


def walk(roots):
    """Every program module the roots import, directly or through each other (the same walk the installers used to depend on)."""
    return set(installer_build.program_modules(list(roots)))


def test_every_program_file_courier_imports_is_on_the_list():
    listed = {f[:-3] for f in courier_files.program()}
    needed = walk(["courier"])
    assert needed <= listed, f"imported by courier.py but not in courier/courier-files.json: {sorted(needed - listed)}"


def test_every_listed_file_exists_and_nothing_is_listed_twice():
    names = courier_files.program() + list(courier_files.data()) + courier_files.setup() + courier_files.installer_only()
    assert len(names) == len(set(names))
    for f in courier_files.program() + courier_files.installer_only():
        assert (ROOT / f).is_file(), f
    for src in courier_files.data().values():
        assert (ROOT / src).is_file(), src
    for f in courier_files.setup():
        assert (ROOT / "courier" / f).is_file(), f


def test_a_listed_program_file_never_imports_the_full_multiproxy():
    for f in courier_files.program():
        text = (ROOT / f).read_text(encoding="utf-8")
        assert not re.search(r"^(?:import|from) (?:multiproxy|proxy)\b", text, flags=re.M), f


def test_the_zip_the_windows_stage_and_the_linux_package_all_carry_the_listed_files():
    zip_names = {n for _, n in build_pkg.collect()}
    assert zip_names == set(courier_files.program()) | set(courier_files.data()) | set(courier_files.setup())
    stage = {f"{m}.py" for m in installer_build.product_modules("courier")}
    assert stage == set(courier_files.program()) | set(courier_files.installer_only())
    assert {dest for dest in installer_build.PRODUCTS["courier"]["data"].values()} == set(courier_files.data())
    linux = {dest for _, dest in linux_build.collect("courier") if dest.endswith(".py")}
    assert linux == set(courier_files.program())


def test_no_consumer_keeps_its_own_copy_any_more():
    ps1 = (ROOT / "courier/setup-courier.ps1").read_text(encoding="utf-8")
    assert "courier-files.json" in ps1 and "'courier_system.py'" not in ps1 and "'gpu_monitor.py'" not in ps1
    iss = (ROOT / "installer/courier.iss").read_text(encoding="utf-8")
    assert "courier_system.py" not in iss and "{#OlderProgramFiles}" in iss
    assert "PROGRAM_FILES = courier_files.program()" in (ROOT / "scripts/build_courier_package.py").read_text(encoding="utf-8")
    assert "/DOlderProgramFiles=" in (ROOT / "installer/build.py").read_text(encoding="utf-8")


def test_the_zip_ships_the_list_for_the_setup_script_and_the_installer_files_are_not_in_it(tmp_path):
    out = tmp_path / build_pkg.ZIP_NAME
    names = build_pkg.build(out)
    assert "courier-files.json" in names and "installer_helper.py" not in names
    with zipfile.ZipFile(out) as z:
        assert z.read("courier-files.json") == (ROOT / "courier/courier-files.json").read_bytes()


def test_the_update_set_is_the_program_and_data_files_only():
    s = set(courier_files.update_set())
    assert "courier.py" in s and "requirements-courier.txt" in s
    assert not s & set(courier_files.setup()) and "installer_helper.py" not in s
    assert not any(n.endswith((".bat", ".ps1", ".sh")) for n in s), "an update never carries a script that could be run"
