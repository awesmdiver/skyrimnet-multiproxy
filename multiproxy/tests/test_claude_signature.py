"""The signature check on the Windows Claude program (claude_courier.signature_ok / ClaudeHost._check_signature), and the whole-version rule.

Real Windows PowerShell on this device with stand-in files: a Microsoft program that is certainly validly signed (notepad.exe) is the "good" file, and copies of it are
broken, renamed or compared against another signer. No real Claude download is checked here (that needs a real download on a real device).
"""
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import claude_courier as cc
from tests.test_claude_courier import FakeReleases, log, world            # noqa: F401  (fixture)

windows_only = pytest.mark.skipif(sys.platform != "win32", reason="Authenticode signatures are a Windows thing")
NOTEPAD = Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "notepad.exe"
MICROSOFT = "Microsoft Windows"            # the signer name Windows reports for notepad.exe


@pytest.fixture
def signed(tmp_path):
    """A copy of a validly signed Microsoft program, in a folder of its own."""
    if not NOTEPAD.is_file():
        pytest.skip("notepad.exe is not here")
    f = tmp_path / "signed.exe"
    shutil.copy2(NOTEPAD, f)
    return f


@windows_only
class TestTheBugAndTheFix:
    def test_the_old_way_of_handing_over_the_path_never_said_ok(self, tmp_path):
        """The bug (security review S9): `-Command <script> <path>` does not bind the path to `$args[0]`, so a certainly-valid file was never accepted
        (PowerShell says "Cannot bind argument to parameter 'LiteralPath' because it is null"). This is the old call, word for word, against a validly signed file."""
        script = ("$s = Get-AuthenticodeSignature -LiteralPath $args[0]; "
                  "if ($s.Status -eq 'Valid' -and $s.SignerCertificate.Subject -match 'Microsoft') { 'ok' } else { 'bad' }")
        out = tmp_path / "said.txt"
        with open(out, "w") as sink:                                  # a file, not a pipe: a stuck child can't keep this test waiting on a pipe
            proc = subprocess.Popen(["powershell", "-NoProfile", "-NonInteractive", "-Command", script, str(NOTEPAD)], stdout=sink, stderr=subprocess.DEVNULL,
                                    stdin=subprocess.DEVNULL, env={k: v for k, v in os.environ.items() if k.upper() != "PSMODULEPATH"})
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)], capture_output=True, timeout=20)      # the one started here, by its own id
                proc.wait(timeout=20)
        said = out.read_text().strip()
        assert said != "ok"

    def test_the_new_way_accepts_that_same_file_when_the_signer_is_the_expected_one(self, signed):
        assert cc.signature_ok(signed, MICROSOFT) is True

    def test_the_path_is_never_part_of_the_command_text(self):
        assert "$args" not in cc.SIGNATURE_SCRIPT and "-LiteralPath $env:" + cc.SIGNATURE_ENV in cc.SIGNATURE_SCRIPT


@windows_only
class TestWhoIsRefused:
    def test_a_file_signed_by_someone_else_is_refused(self, signed):
        assert cc.signature_ok(signed) is False, "notepad.exe is validly signed, but not by Anthropic, PBC"
        assert cc.signature_ok(signed, "Anthropic, PBC") is False

    @pytest.mark.parametrize("expected", ["Microsoft", "microsoft windows", "Microsoft Windows ", "Windows", "Anthropic", ""])
    def test_the_signer_is_compared_exactly_not_by_a_piece_of_its_name(self, signed, expected):
        assert cc.signature_ok(signed, expected) is False

    def test_an_unsigned_file_is_refused(self, tmp_path):
        f = tmp_path / "fake.exe"
        f.write_text("this is only text")
        assert cc.signature_ok(f, MICROSOFT) is False

    def test_a_signed_file_with_one_changed_byte_is_refused(self, signed):
        data = bytearray(signed.read_bytes())
        data[len(data) // 2] ^= 0xFF
        signed.write_bytes(bytes(data))
        assert cc.signature_ok(signed, MICROSOFT) is False

    def test_a_missing_file_and_an_unreachable_powershell_are_not_trusted(self, tmp_path, monkeypatch):
        assert cc.signature_ok(tmp_path / "nothing.exe", MICROSOFT) is False
        monkeypatch.setattr(cc, "_powershell", lambda: str(tmp_path / "no-such-powershell.exe"))
        assert cc.signature_ok(NOTEPAD, MICROSOFT) is False

    def test_an_answer_that_cannot_be_read_is_not_trusted(self, signed, monkeypatch):
        class R:
            returncode, stdout = 0, "this is not json"
        monkeypatch.setattr(cc.hidden_process, "run", lambda *a, **k: R())
        assert cc.signature_ok(signed, MICROSOFT) is False

    def test_an_empty_expected_signer_trusts_nothing(self, signed):
        assert cc.signature_ok(signed, "") is False


@windows_only
class TestPathsWithOddCharacters:
    @pytest.mark.parametrize("name", ["with spaces.exe", "it's quoted.exe", "dollar $env and `tick`.exe", "semi;colon & amp.exe", "[brackets] (and) {braces}.exe", "100% sure.exe"])
    def test_a_path_with_spaces_and_quotes_works(self, tmp_path, name):
        folder = tmp_path / "a folder with 'quotes' and spaces"
        folder.mkdir()
        f = folder / name
        shutil.copy2(NOTEPAD, f)
        assert cc.signature_ok(f, MICROSOFT) is True


@windows_only
@pytest.mark.asyncio
class TestInsideTheInstall:
    async def test_a_refused_file_is_deleted_and_says_the_line(self, world, tmp_path):
        host = world.make()
        host.expected_signer = "Anthropic, PBC"
        part = tmp_path / "claude.part"
        shutil.copy2(NOTEPAD, part)                                   # validly signed, but by Microsoft
        with pytest.raises(cc.ClaudeError) as e:
            await host._check_signature(part)
        assert e.value.message == cc.LINE_SIGNATURE == "The download didn't pass Anthropic's security check, so it was deleted." and e.value.status == 502
        assert not part.exists()

    async def test_an_unsigned_download_is_deleted(self, world, tmp_path):
        host = world.make()
        part = tmp_path / "claude.part"
        part.write_bytes(b"MZ not a real program")
        with pytest.raises(cc.ClaudeError):
            await host._check_signature(part)
        assert not part.exists()

    async def test_a_file_from_the_expected_signer_is_kept(self, world, tmp_path):
        host = world.make()
        host.expected_signer = MICROSOFT
        part = tmp_path / "claude.part"
        shutil.copy2(NOTEPAD, part)
        await host._check_signature(part)
        assert part.exists()


@pytest.mark.asyncio
class TestTheConstants:
    def test_the_production_signer_is_anthropics_as_their_own_page_says(self, world):
        assert cc.EXPECTED_SIGNER == "Anthropic, PBC"
        assert world.make().expected_signer == "Anthropic, PBC"

    def test_the_check_runs_on_a_real_windows_install_only(self, world):
        assert cc.ClaudeHost(Path("x"), log).verify_signature is (sys.platform == "win32")


@pytest.mark.asyncio
class TestTheWholeVersion:
    @pytest.mark.parametrize("text,ok", [("2.1.211", True), ("9.9.9", True), ("2.2.0-beta.1", True), ("2.1.211 ", False), ("2.1.211\n2.1.212", False), ("", False),
                                          ("v2.1.211", False), ("2.1", False), ("2.1.211/../x", False), ("../2.1.211", False), ("2.1.211 && calc", False),
                                          ("2.1.211?x=1", False), ("2.1.211\\x", False)])
    def test_only_a_whole_version_counts(self, text, ok):
        assert cc.is_version(text) is ok

    async def test_the_newest_version_must_be_a_whole_version(self, world):
        for bad in ("9.9.9 junk", "9.9.9/../../evil", "<html>9.9.9</html>"):
            world.releases.version = bad
            with pytest.raises(cc.ClaudeError):
                await world.host.fetch_latest()
        world.releases.version = "9.9.9"
        assert await world.host.fetch_latest() == "9.9.9"
