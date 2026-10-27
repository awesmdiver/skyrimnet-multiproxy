"""The release signer (scripts/sign_courier_package.py). Only throwaway keys, made inside pytest's scratch folder, are ever used; the director's real key
is never generated, read or needed here."""
import base64
import importlib.util
import io
import os
import shutil
import sys
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
import courier_files  # noqa: E402
import courier_keys  # noqa: E402
import update_verify as uv  # noqa: E402

spec = importlib.util.spec_from_file_location("sign_tool", ROOT / "scripts" / "sign_courier_package.py")
tool = importlib.util.module_from_spec(spec)
spec.loader.exec_module(tool)


def set_staged_keys(staged, current, nxt=""):
    """The keys the staged courier_keys.py carries (the stage is a copy of the real file; the scratch key stands in for the director's)."""
    import re as _re
    f = staged / "courier_keys.py"
    text = f.read_text(encoding="utf-8")
    text = _re.sub(r"^CURRENT = .*$", f'CURRENT = "{current}"', text, flags=_re.M)
    text = _re.sub(r"^NEXT = .*$", f'NEXT = "{nxt}"', text, flags=_re.M)
    f.write_text(text, encoding="utf-8")


@pytest.fixture()
def world(tmp_path, monkeypatch):
    """A scratch key (made by the tool itself), a staged copy of the real Courier files, a scratch 'last released' file, and the key list set to the scratch key."""
    keys = tmp_path / "keys"
    public = tool.generate_key(keys)
    key_file = keys / tool.KEY_FILE_NAME
    staged = tmp_path / "staged"
    staged.mkdir()
    for name in courier_files.program():
        shutil.copyfile(ROOT / name, staged / name)
    for name, src in courier_files.data().items():
        shutil.copyfile(ROOT / src, staged / name)
    monkeypatch.setattr(courier_keys, "CURRENT", public)
    monkeypatch.setattr(courier_keys, "NEXT", "")
    set_staged_keys(staged, public)
    release = tmp_path / "last.txt"
    release.write_text("# comment\n4\n", encoding="utf-8")
    monkeypatch.setattr(tool, "RELEASE_FILE", release)
    return {"public": public, "key": key_file, "staged": staged, "release": release, "out": tmp_path / "out" / "pkg.zip", "tmp": tmp_path}


def sign(w, **kw):
    return tool.sign(w["staged"], w["key"], w["out"], **kw)


def refused(fn, *a, **kw):
    with pytest.raises(tool.Refused) as e:
        fn(*a, **kw)
    return str(e.value)


def test_generate_prints_only_the_public_key_and_keeps_the_private_one_in_its_file(world, capsys, tmp_path):
    folder = tmp_path / "more-keys"
    assert tool.main(["--generate-key", str(folder)]) == 0
    out = capsys.readouterr()
    seed_b64 = (folder / tool.KEY_FILE_NAME).read_text(encoding="utf-8").splitlines()[1]
    assert seed_b64 not in out.out + out.err, "the private key must never be printed"
    public = out.out.strip().splitlines()[-1]
    assert base64.b64decode(public) == uv.ed25519_public_key(base64.b64decode(seed_b64))
    assert not tool.readable_by_others(folder / tool.KEY_FILE_NAME)
    assert "already" in refused(tool.generate_key, folder), "an existing key is never overwritten"


def test_a_key_folder_inside_the_project_or_any_git_tree_is_refused_and_nothing_is_made(tmp_path):
    inside = ROOT / "zz-key-folder-that-must-not-be-made"
    assert "inside a repository" in refused(tool.generate_key, inside)
    assert not inside.exists()
    other_repo = tmp_path / "somewhere" / "work"
    (tmp_path / "somewhere" / ".git").mkdir(parents=True)
    assert "inside a repository" in refused(tool.generate_key, other_repo)
    assert not other_repo.exists()


def test_a_key_file_inside_a_repository_is_refused_at_signing(world):
    (world["tmp"] / ".git").mkdir()
    moved = world["tmp"] / "inside" / tool.KEY_FILE_NAME
    moved.parent.mkdir()
    shutil.copyfile(world["key"], moved)
    assert "inside a repository" in refused(tool.sign, world["staged"], moved, world["out"])
    assert "inside a repository" in refused(tool.sign, world["staged"], ROOT / "never-there.txt", world["out"])
    assert not world["out"].exists()


def test_a_key_other_accounts_can_read_is_refused(world):
    if os.name == "nt":
        import subprocess
        subprocess.run(["icacls", str(world["key"]), "/grant", "Everyone:R"], capture_output=True, check=True)
    else:
        os.chmod(world["key"], 0o644)
    assert "Other accounts" in refused(sign, world)
    assert not world["out"].exists()


def test_a_file_that_is_not_a_key_is_refused_without_quoting_it(world):
    world["key"].write_text("SECRETLOOKINGTEXT\n", encoding="utf-8")
    msg = refused(sign, world)
    assert "SECRETLOOKINGTEXT" not in msg and "not a Courier signing key" in msg


def test_sign_then_verify_with_the_scratch_key_and_the_real_courier_files(world):
    info = sign(world)
    assert info["build"] == uv.read_courier_build((ROOT / "courier.py").read_bytes())
    manifest, members = uv.verify_package(world["out"], keys=[base64.b64decode(world["public"])], running_build=4)
    assert set(members) == set(courier_files.update_set())
    assert manifest["files"] == sorted(manifest["files"], key=lambda f: f["name"])
    with zipfile.ZipFile(world["out"]) as z:
        assert set(z.namelist()) == set(courier_files.update_set()) | {"manifest.json", "manifest.sig"}


def test_the_zip_keeps_no_dates_of_its_own(world):
    sign(world)
    with zipfile.ZipFile(world["out"]) as z:
        assert all(i.date_time == (1980, 1, 1, 0, 0, 0) for i in z.infolist())


def test_the_stamp_file_is_allowed_but_any_other_file_or_folder_is_refused(world):
    (world["staged"] / "courier-build.txt").write_text("2026-10-24-abc\n", encoding="utf-8")
    assert "courier-build.txt" in uv.verify_package(tool.sign(world["staged"], world["key"], world["out"]) and world["out"], keys=[base64.b64decode(world["public"])])[1]
    (world["staged"] / "courier.json").write_text("{}", encoding="utf-8")
    assert "courier.json" in refused(sign, world)
    (world["staged"] / "courier.json").unlink()
    (world["staged"] / "extra").mkdir()
    assert "folder" in refused(sign, world)
    (world["staged"] / "extra").rmdir()
    (world["staged"] / "other_pcs.py").unlink()
    assert "other_pcs.py" in refused(sign, world)


def test_a_build_not_above_the_last_released_is_refused(world):
    build = uv.read_courier_build((ROOT / "courier.py").read_bytes())
    world["release"].write_text(f"{build}\n", encoding="utf-8")
    assert "already released" in refused(sign, world)
    world["release"].write_text(f"{build + 3}\n", encoding="utf-8")
    assert "already released" in refused(sign, world)
    world["release"].write_text("not a number\n", encoding="utf-8")
    assert "Cannot read" in refused(sign, world)
    assert not world["out"].exists()


def test_a_key_the_courier_does_not_carry_is_refused(world, monkeypatch):
    monkeypatch.setattr(courier_keys, "CURRENT", courier_keys.TEST_PUBLIC_KEY)
    msg = refused(sign, world, allow_test_key=True)
    assert "not the CURRENT or NEXT" in msg


def test_while_current_is_still_the_public_test_key_nothing_is_signed(world, monkeypatch):
    seed_b64 = world["key"].read_text(encoding="utf-8").splitlines()[1]
    monkeypatch.setattr(courier_keys, "CURRENT", courier_keys.TEST_PUBLIC_KEY)
    monkeypatch.setattr(courier_keys, "NEXT", world["public"])           # a real NEXT does not make a test CURRENT acceptable
    assert "TEST key" in refused(sign, world)
    assert seed_b64 not in refused(sign, world)
    assert tool.sign(world["staged"], world["key"], world["out"], allow_test_key=True)["build"]


def test_the_next_key_can_sign_when_current_is_real(world, monkeypatch, tmp_path):
    second = tmp_path / "second"
    next_public = tool.generate_key(second)
    monkeypatch.setattr(courier_keys, "NEXT", next_public)
    set_staged_keys(world["staged"], world["public"], next_public)
    assert tool.sign(world["staged"], second / tool.KEY_FILE_NAME, world["out"])["build"]
    assert uv.verify_package(world["out"])[0]["build"]            # the default keys: CURRENT and NEXT


def test_tampering_with_the_written_package_is_caught(world):
    sign(world)
    keys = [base64.b64decode(world["public"])]
    good = world["out"].read_bytes()

    def rebuilt(change):
        src, dst = io.BytesIO(good), io.BytesIO()
        with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w") as zout:
            for info in zin.infolist():
                data = change(info.filename, zin.read(info))
                if data is not None:
                    zout.writestr(info.filename, data)
            change(None, None)
        return dst.getvalue()

    assert uv.verify_package(good, keys=keys)
    flipped = rebuilt(lambda n, d: (d[:-1] + bytes([d[-1] ^ 1])) if n == "other_pcs.py" else d)
    with pytest.raises(uv.UpdateRejected) as e:
        uv.verify_package(flipped, keys=keys)
    assert e.value.code == "damaged"
    gone = rebuilt(lambda n, d: None if n == "gguf_header.py" else d)
    with pytest.raises(uv.UpdateRejected) as e:
        uv.verify_package(gone, keys=keys)
    assert e.value.code == "damaged"
    manifest_changed = rebuilt(lambda n, d: d.replace(b'"build": ', b'"build": 1') if n == "manifest.json" else d)
    with pytest.raises(uv.UpdateRejected) as e:
        uv.verify_package(manifest_changed, keys=keys)
    assert e.value.code == "bad_signature"


def test_a_package_with_a_wrong_build_in_courier_py_is_not_signed_as_one(world):
    """The manifest build comes from courier.py itself, so the two cannot differ; a hand-made package where they do is refused by the check."""
    fs = tool.gather(world["staged"])
    manifest = tool.build_manifest(fs)
    import json
    wrong = json.dumps(dict(json.loads(manifest), build=json.loads(manifest)["build"] + 1), sort_keys=True).encode()
    seed = base64.b64decode(world["key"].read_text(encoding="utf-8").splitlines()[1])
    tool.write_package(world["out"], wrong, uv.ed25519_sign(seed, wrong), fs)
    with pytest.raises(uv.UpdateRejected) as e:
        uv.verify_package(world["out"], keys=[base64.b64decode(world["public"])])
    assert e.value.code == "wrong_build"


def test_verify_and_mark_released_from_the_command_line(world, capsys):
    sign(world)
    assert tool.main(["--verify", str(world["out"]), "--pubkey", world["public"]]) == 0
    assert "OK: build" in capsys.readouterr().out
    assert tool.main(["--verify", str(world["out"]), "--pubkey", base64.b64encode(bytes(32)).decode()]) == 1
    assert "bad_signature" in capsys.readouterr().out
    assert tool.main(["--mark-released", str(world["out"]), "--pubkey", world["public"]]) == 0
    assert tool.last_released_build() == uv.read_courier_build((ROOT / "courier.py").read_bytes())
    assert tool.main(["--mark-released", str(world["out"]), "--pubkey", world["public"]]) == 2, "the same build is not recorded twice"
    assert tool.main(["--verify", str(world["out"]), "--pubkey", "not base64!"]) == 2


def test_the_command_line_signs_and_a_refusal_exits_with_two(world, capsys):
    assert tool.main(["--sign", str(world["staged"]), "--key", str(world["key"]), "--out", str(world["out"])]) == 0
    assert "Signed build" in capsys.readouterr().out
    world["release"].write_text("99\n", encoding="utf-8")
    assert tool.main(["--sign", str(world["staged"]), "--key", str(world["key"]), "--out", str(world["out"])]) == 2
    assert "Refused" in capsys.readouterr().err
    assert tool.main(["--sign", str(world["staged"])]) == 2


def test_the_committed_last_released_build_is_a_readable_number():
    spec2 = importlib.util.spec_from_file_location("sign_tool2", ROOT / "scripts" / "sign_courier_package.py")
    fresh = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(fresh)
    assert isinstance(fresh.last_released_build(), int)


def test_the_installer_stage_can_be_signed_as_it_is_and_leaves_its_own_helper_out(world, tmp_path):
    """The director signs what installer/build.py staged: it also holds installer_helper.py, which is the installer's, not part of an update."""
    spec3 = importlib.util.spec_from_file_location("installer_build_for_sign", ROOT / "installer" / "build.py")
    ib = importlib.util.module_from_spec(spec3)
    spec3.loader.exec_module(ib)
    stage = tmp_path / "stage"
    names = ib.stage_program("courier", stage)
    assert "installer_helper.py" in names and "courier-build.txt" in names
    set_staged_keys(stage, world["public"])          # (the stage is a copy of the real courier_keys.py; the scratch key stands in for the director's)
    tool.sign(stage, world["key"], world["out"])
    members = uv.verify_package(world["out"], keys=[base64.b64decode(world["public"])])[1]
    assert "installer_helper.py" not in members and "courier-build.txt" in members and set(courier_files.update_set()) <= set(members)


# ---- security review of the 4.0 release, M2: what the staged Courier trusts, and where a key may never end up ----------------------------------------------
def test_a_stage_built_before_the_real_key_was_pasted_in_is_not_signed(world):
    set_staged_keys(world["staged"], courier_keys.TEST_PUBLIC_KEY)          # the project's copy is right, but these staged files still trust the public test key
    assert "do not carry the public key" in refused(sign, world)
    assert not world["out"].exists()


def test_a_stage_that_carries_the_test_key_as_next_is_not_signed(world):
    set_staged_keys(world["staged"], world["public"], courier_keys.TEST_PUBLIC_KEY)
    assert "public test key" in refused(sign, world)
    assert not world["out"].exists()


def test_a_stage_whose_key_file_cannot_be_read_is_not_signed(world):
    (world["staged"] / "courier_keys.py").write_text("CURRENT = (\n", encoding="utf-8")
    assert "could not be read" in refused(sign, world)


def test_a_stage_that_carries_the_same_key_the_signature_is_made_with_is_signed(world):
    assert sign(world)["build"]


def test_a_staged_file_that_looks_like_a_signing_key_is_refused(world):
    (world["staged"] / "courier.py").write_text((ROOT / "courier.py").read_text(encoding="utf-8") + "\n# " + tool.KEY_HEADER + "\n", encoding="utf-8")
    assert "looks like a signing key" in refused(sign, world)


def test_the_key_file_name_can_never_be_committed_or_packaged():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "courier-signing-key*" in ignore and "*.pem" in ignore
    sys.path.insert(0, str(ROOT / "installer"))
    import build as installer_build
    import build_courier_package as package_build
    assert tool.KEY_FILE_NAME in installer_build.NEVER and tool.KEY_FILE_NAME in package_build.NEVER


# ---- the start program (2026-10-26): it sits in the installer's stage, never goes into an update, and does not stop the signing ----
def test_the_start_program_is_left_out_of_the_update_and_said_so(world, tmp_path):
    stage = tmp_path / "stage-with-exe"
    shutil.copytree(world["staged"], stage)
    (stage / "MultiProxy Courier.exe").write_bytes(b"MZ not a real program")
    assert "MultiProxy Courier.exe" in courier_files.installer_only_files()
    info = tool.sign(stage, world["key"], world["out"])
    members = uv.verify_package(world["out"], keys=[base64.b64decode(world["public"])])[1]
    assert "MultiProxy Courier.exe" not in members and set(courier_files.update_set()) <= set(members)
    assert info["left_out"] == ["MultiProxy Courier.exe"]


def test_the_command_line_prints_the_left_out_line(world, tmp_path, capsys):
    stage = tmp_path / "stage-line"
    shutil.copytree(world["staged"], stage)
    (stage / "MultiProxy Courier.exe").write_bytes(b"MZ")
    assert tool.main(["--sign", str(stage), "--key", str(world["key"]), "--out", str(world["out"])]) == 0
    assert "Left out of the update (the installers carry it): MultiProxy Courier.exe." in capsys.readouterr().out


def test_an_unknown_file_next_to_the_start_program_is_still_refused(world, tmp_path):
    stage = tmp_path / "stage-stray"
    shutil.copytree(world["staged"], stage)
    (stage / "MultiProxy Courier.exe").write_bytes(b"MZ")
    (stage / "other-tool.exe").write_bytes(b"MZ")
    with pytest.raises(tool.Refused, match="not part of an update"):
        tool.sign(stage, world["key"], world["out"])
    assert not Path(world["out"]).exists()
