"""Which version and build a program is, in one plain line (build_info.py): the tray menu, the About line, the log's first line, the Courier's status."""
from pathlib import Path

import build_info
import courier as cr
import settings_ui

ROOT = Path(__file__).resolve().parent.parent


def test_a_package_has_a_stamp_and_a_source_copy_says_so(tmp_path):
    assert build_info.read_stamp(tmp_path) == "" and build_info.version_line("3.2.0", "") == "MultiProxy 3.2.0 (not built from a package)"
    (tmp_path / "multiproxy-build.txt").write_text("2026-10-06-abc1234\n", encoding="utf-8")
    assert build_info.read_stamp(tmp_path) == "2026-10-06-abc1234"
    assert build_info.version_line("3.2.0", "2026-10-06-abc1234") == "MultiProxy 3.2.0, built 2026-10-06-abc1234"


def test_the_server_writes_the_line_first_and_the_about_page_carries_the_build():
    src = (ROOT / "multiproxy.py").read_text(encoding="utf-8")
    assert "async def lifespan(app):\n    logger.info(_build_info.version_line(PROXY_VERSION, BUILD_STAMP))" in src.replace("\r\n", "\n")
    assert '"about": {"version": PROXY_VERSION, "build": BUILD_STAMP,' in src
    assert "(a.build ? ', built ' + esc(a.build) : '')" in settings_ui.SETTINGS_JS if hasattr(settings_ui, "SETTINGS_JS") else "(a.build ? ', built ' + esc(a.build) : '')" in (ROOT / "settings_ui.py").read_text(encoding="utf-8")


def test_the_installer_build_writes_the_stamp_for_both_programs():
    src = (ROOT / "installer" / "build.py").read_text(encoding="utf-8")
    assert 'name in ("courier", "multiproxy")' in src and '"multiproxy-build.txt"' in src


def test_the_couriers_status_names_the_build():
    import inspect
    assert "{', build ' + str(build) if build is not None else ''} is running." in inspect.getsource(cr.status_text)
