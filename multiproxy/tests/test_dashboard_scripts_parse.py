"""Every <script> block of the dashboard page must be valid JavaScript (2026-10-03).
A stray apostrophe in a single-quoted string in the Voice words killed the whole script: no tab worked.
This serves the page the way the server does and syntax-checks each inline script with Node, so any UI item that breaks the page fails here.
"""
import re
import shutil
import subprocess

import pytest

NODE = shutil.which("node")
needs_node = pytest.mark.skipif(NODE is None, reason="Node isn't installed")


@needs_node
def test_every_dashboard_script_parses(test_client, tmp_path):
    html = test_client.get("/").text
    blocks = re.findall(r"<script(?![^>]*\bsrc=)[^>]*>(.*?)</script>", html, re.S)
    assert blocks, "no inline scripts found in the dashboard page"
    for i, src in enumerate(blocks):
        f = tmp_path / f"block{i}.js"
        f.write_text(src, encoding="utf-8")
        r = subprocess.run([NODE, "--check", str(f)], capture_output=True, text=True, timeout=60)
        assert r.returncode == 0, f"script block {i} has a syntax error:\n{r.stderr[:800]}"
