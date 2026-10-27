"""Starting MultiProxy must not print a SyntaxWarning (2026-10-25: an unescaped backslash in a docstring did). A future Python makes that warning an error, and MultiProxy would not start.
Every top-level program file is compiled with warnings turned into errors, so a bad escape cannot come back unnoticed."""
import pathlib
import warnings

import pytest

ROOT = pathlib.Path(__file__).resolve().parent.parent
FILES = sorted(ROOT.glob("*.py"))


@pytest.mark.parametrize("path", FILES, ids=lambda p: p.name)
def test_program_file_compiles_without_a_syntax_warning(path):
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        compile(path.read_text(encoding="utf-8"), str(path), "exec")


def test_the_scan_sees_the_program_files():
    assert len(FILES) > 100 and ROOT / "multiproxy.py" in FILES
