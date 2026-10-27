"""The 4.1 build's pinned runtime: the lock files, the cache key, and the OSV check that stops a build on a known vulnerability (no internet: OSV is a stand-in)."""
import importlib.util
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))
import osv_check  # noqa: E402


def load(rel, name):
    spec = importlib.util.spec_from_file_location(name, ROOT / rel)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


bd = load("installer/build.py", "lock_bd")
mk = load("scripts/make_runtime_lock.py", "lock_mk")


def entries(lock: Path):
    """[(name, version, [hashes])] from a lock file."""
    text = lock.read_text(encoding="utf-8")
    out = []
    for block in re.findall(r"^([A-Za-z0-9_.-]+)==(\S+) \\\n((?:\s+--hash=sha256:[0-9a-f]{64}(?: \\)?\n?)+)", text, re.M):
        out.append((block[0], block[1], re.findall(r"[0-9a-f]{64}", block[2])))
    return out


@pytest.mark.parametrize("name", ["multiproxy", "courier"])
def test_every_package_in_a_lock_is_pinned_with_hashes(name):
    lock = ROOT / bd.PRODUCTS[name]["lock"]
    text = lock.read_text(encoding="utf-8")
    pkgs = entries(lock)
    body = [l for l in text.splitlines() if l and not l.startswith("#") and not l.startswith(" ")]
    assert pkgs and len(pkgs) == len(body), "every package line is name==version followed by hashes, nothing loose"
    assert all(h for _, _, h in pkgs) and not re.search(r"[<>~]=|^\s*[a-z].*[^=]=[^=]", "\n".join(body))


@pytest.mark.parametrize("name,reqs", [("multiproxy", ["requirements.txt", "requirements-tray.txt"]), ("courier", ["requirements-courier.txt"])])
def test_a_lock_holds_every_package_its_requirements_name(name, reqs):
    locked = {n.lower().replace("_", "-") for n, _, _ in entries(ROOT / bd.PRODUCTS[name]["lock"])}
    wanted = set()
    for r in reqs:
        for line in (ROOT / r).read_text(encoding="utf-8").splitlines():
            m = re.match(r"^([A-Za-z0-9_.-]+)", line.strip())
            if m and not line.startswith("#"):
                wanted.add(m.group(1).lower().replace("_", "-"))
    assert wanted <= locked


def test_the_runtime_cache_is_keyed_on_the_lock_files_text(tmp_path, monkeypatch):
    seen = []
    monkeypatch.setattr(bd, "CACHE", tmp_path / "cache")
    monkeypatch.setattr(bd, "build_runtime", lambda name, dest: (dest.mkdir(parents=True), (dest / "python.exe").write_text("x"), seen.append(dest)))
    lock = tmp_path / "x.lock.txt"
    lock.write_text("aiohttp==1.0 \\\n    --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    monkeypatch.setattr(bd, "ROOT", tmp_path)
    monkeypatch.setitem(bd.PRODUCTS["courier"], "lock", "x.lock.txt")
    first = bd.runtime_for("courier")
    assert bd.runtime_for("courier") == first and len(seen) == 1, "the same lock reuses the cached runtime"
    lock.write_text("aiohttp==2.0 \\\n    --hash=sha256:" + "b" * 64 + "\n", encoding="utf-8")
    assert bd.runtime_for("courier") != first and len(seen) == 2, "a moved pin builds a fresh one"


def test_only_windows_wheels_for_this_python_get_their_hashes_into_a_lock():
    ok = ["aiohttp-3.14.5-cp314-cp314-win_amd64.whl", "pillow-13.0.0-cp314-cp314-win_amd64.whl", "x-1-py3-none-any.whl", "pystray-0.19.5-py2.py3-none-any.whl", "pydantic_core-2.50.1-cp314-cp314-win_amd64.whl",
          "cryptography-1-cp311-abi3-win_amd64.whl".replace("cp311", "cp314")]
    no = ["aiohttp-3.14.5.tar.gz", "aiohttp-3.14.5-cp314-cp314-manylinux_2_28_x86_64.whl", "aiohttp-3.14.5-cp314-cp314-win32.whl", "aiohttp-3.14.5-cp313-cp313-win_amd64.whl",
          "aiohttp-3.14.5-cp314-cp314-win_arm64.whl", "aiohttp-3.14.5-cp314-cp314t-win_amd64.whl"]
    assert all(mk._wheel_ok(f) for f in ok) and not any(mk._wheel_ok(f) for f in no)


def test_the_embedded_pythons_pin_is_the_one_the_lock_tooling_targets():
    assert bd.PY_VERSION.startswith(mk.PY_MINOR + ".") and f"cp{bd.PY_TAG}" == mk.PY_TAG


# --- OSV ---------------------------------------------------------------------------------------------------------------------------------
def make_site(tmp_path, pkgs):
    sp = tmp_path / "site-packages"
    sp.mkdir()
    for n, v in pkgs:
        (sp / f"{n}-{v}.dist-info").mkdir()
    return sp


def fake_osv(db):
    """post/get stand-ins: db = {(package, version): [vuln dict, ...]}."""
    def post(path, body):
        assert path == "/querybatch" and all(set(q) == {"package", "version"} for q in body["queries"]), "only names and versions are sent"
        return {"results": [{"vulns": [{"id": v["id"]} for v in db.get((q["package"]["name"], q["version"]), [])]} for q in body["queries"]]}

    def get(path):
        vid = path.rsplit("/", 1)[1]
        for vs in db.values():
            for v in vs:
                if v["id"] == vid:
                    return v
    return post, get


def vuln(vid, package, fixed=None, withdrawn=False):
    ev = [{"introduced": "0"}] + ([{"fixed": fixed}] if fixed else [])
    v = {"id": vid, "summary": f"{vid} summary", "affected": [{"package": {"name": package, "ecosystem": "PyPI"}, "ranges": [{"type": "ECOSYSTEM", "events": ev}]}]}
    if withdrawn:
        v["withdrawn"] = "2026-10-01T00:00:00Z"
    return v


def test_installed_reads_names_and_versions_from_the_dist_info_folders(tmp_path):
    sp = make_site(tmp_path, [("aiohttp", "3.14.5"), ("pydantic_core", "2.50.1"), ("Pillow", "13.0.0")])
    assert osv_check.installed(sp) == [("aiohttp", "3.14.5"), ("pillow", "13.0.0"), ("pydantic-core", "2.50.1")]


def test_a_known_vulnerability_with_a_fix_is_reported_and_the_others_are_not():
    db = {("aiohttp", "3.14.3"): [vuln("GHSA-aaaa", "aiohttp", fixed="3.14.4"), vuln("GHSA-gone", "aiohttp", fixed="3.14.4", withdrawn=True), vuln("GHSA-nofix", "aiohttp")],
          ("pillow", "13.0.0"): []}
    post, get = fake_osv(db)
    found = osv_check.check([("aiohttp", "3.14.3"), ("pillow", "13.0.0")], post=post, get=get)
    assert [(f["package"], f["id"], f["fixed_in"]) for f in found] == [("aiohttp", "GHSA-aaaa", ["3.14.4"])]
    assert "aiohttp 3.14.3" in osv_check.describe(found) and "3.14.4" in osv_check.describe(found)


def test_the_build_stops_on_a_finding_and_names_it(tmp_path, monkeypatch):
    runtime = tmp_path / "rt"
    (runtime / "Lib" / "site-packages" / "aiohttp-3.14.3.dist-info").mkdir(parents=True)
    post, get = fake_osv({("aiohttp", "3.14.3"): [vuln("GHSA-aaaa", "aiohttp", fixed="3.14.4")]})
    monkeypatch.setattr(osv_check, "_post", post)
    monkeypatch.setattr(osv_check, "_get", get)
    with pytest.raises(bd.BuildError) as e:
        bd.osv_gate(runtime)
    assert "aiohttp 3.14.3" in str(e.value) and "GHSA-aaaa" in str(e.value) and "3.14.4" in str(e.value)


def test_a_clean_runtime_passes_and_an_unreachable_osv_stops_the_build_unless_a_trial_skips_it(tmp_path, monkeypatch, capsys):
    sp = tmp_path / "rt" / "Lib" / "site-packages"
    sp.mkdir(parents=True)
    (sp / "aiohttp-3.14.5.dist-info").mkdir()
    post, get = fake_osv({})
    monkeypatch.setattr(osv_check, "_post", post)
    monkeypatch.setattr(osv_check, "_get", get)
    bd.osv_gate(tmp_path / "rt")
    assert "no known vulnerability" in capsys.readouterr().out

    def down(*a, **k):
        raise osv_check.OsvUnavailable("no route to host")
    monkeypatch.setattr(osv_check, "_post", down)
    with pytest.raises(bd.BuildError) as e:
        bd.osv_gate(tmp_path / "rt")
    assert "--skip-osv-check" in str(e.value)
    bd.osv_gate(tmp_path / "rt", skip=True)
    assert "SKIPPED" in capsys.readouterr().out


def test_a_release_build_cannot_skip_the_check(capsys):
    assert bd.main(["--skip-osv-check", "--require-courier-update"]) == 1
    assert "always runs the known-vulnerability check" in capsys.readouterr().err
