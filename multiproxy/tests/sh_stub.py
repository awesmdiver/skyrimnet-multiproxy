"""For the tests that run one tab's script in Node without the whole page: the Start here helpers (start_here_ui.py) stubbed to do nothing,
so a tab's own text can be checked as it was. The real helpers are tested in test_start_here_hide.py."""
SH_STUB = "const SH_HIDDEN = []; function shBox(name, html) { return html; } function shGuidesRow() { return ''; }\n"
